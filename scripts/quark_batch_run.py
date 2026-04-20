"""Quark batch runner: create folder → save a list of share URLs → generate encrypted permanent share links.

Idempotency:
- Cross-batch URL dedup: checks link_registry.json before processing; skips if URL was
  successfully transferred+shared in any previous batch.
- Per-batch recovery: if the script is interrupted and re-run, already-transferred
  and already-shared items are skipped; only missing items are processed.
- State directory: /root/.openclaw/workspace/batch_run_states/

Usage example:
  cd "$QUARK_PAN_TOOL_ROOT"
  . .venv/bin/activate
  python /path/to/skills/quark-mswnlz-publisher/scripts/quark_batch_run.py \
    --items-json /path/to/items.json \
    --out-json /path/to/batch_share_results.json

Environment:
- Run this script inside the QuarkPanTool repo, or set QUARK_PAN_TOOL_ROOT.
- Cookies/config are expected under <quark-root>/config/.
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

from _common import get_quark_root, load_env_files, prepend_sys_path
from _state import (
    BATCH_RUN_STATES_DIR,
    get_share_from_registry,
    is_item_complete_for_batch,
    is_url_processed,
    load_quark_run_state,
    mark_shared,
    mark_transferred,
    recover_share_results,
    register_url,
)

load_env_files()
QUARK_ROOT = get_quark_root(require=True)
prepend_sys_path(QUARK_ROOT)

from quark import QuarkPanFileManager
from utils import read_config


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


async def get_share_id_with_retry(mgr: QuarkPanFileManager, task_id: str, timeout_sec: int = 90, interval_sec: int = 2) -> str:
    """Poll Quark share task until share_id is ready."""
    deadline = time.monotonic() + timeout_sec
    last_error = None
    while time.monotonic() < deadline:
        try:
            share_id = await mgr.get_share_id(task_id)
            if share_id:
                return share_id
        except Exception as exc:  # noqa: BLE001 - upstream API is noisy/inconsistent
            last_error = exc
        await asyncio.sleep(interval_sec)
    if last_error:
        raise TimeoutError(f"等待 share_id 超时（task_id={task_id}），最后一次错误：{last_error}")
    raise TimeoutError(f"等待 share_id 超时（task_id={task_id}）")


async def run_with_timeout(coro, *, timeout_sec: int, stage: str):
    try:
        return await asyncio.wait_for(coro, timeout=timeout_sec)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"阶段超时：{stage}（>{timeout_sec}s）") from exc


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--label", default="短裤哥批次")
    p.add_argument("--month", default="")
    p.add_argument("--items-json", required=True)
    p.add_argument("--out-json", required=True)
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--transfer-timeout", type=int, default=600, help="单条链接转存阶段超时秒数")
    p.add_argument("--share-timeout", type=int, default=180, help="单个分享任务等待超时秒数")
    return p.parse_args()


async def list_existing_files(mgr: QuarkPanFileManager, dir_fid: str) -> dict:
    """Return {name: fid} for all files already in the target directory."""
    existing = {}
    page = 1
    size = 200
    while True:
        data = await mgr.get_sorted_file_list(pdir_fid=dir_fid, page=str(page), size=str(size), fetch_total='true')
        status = data.get('status')
        code = data.get('code')
        if status != 200 or code not in (0, None):
            log(f"[WARN] 列出已有文件失败 page={page}: status={status} code={code}")
            break
        lst = (data.get('data') or {}).get('list') or []
        for item in lst:
            fname = item.get('file_name', '')
            ffid = item.get('fid', '')
            if fname and ffid:
                existing[fname] = ffid
        meta = data.get('metadata') or {}
        total = meta.get('_total') or 0
        _size = meta.get('_size') or size
        if _size * page >= total:
            break
        page += 1
    return existing


async def main():
    args = parse_args()
    os.chdir(QUARK_ROOT)
    log(f"工作目录切换到 QuarkPanTool：{QUARK_ROOT}")

    items = json.loads(Path(args.items_json).read_text(encoding="utf-8"))
    norm_items = []
    skipped_cross_batch = []
    for it in items:
        item_id = (it.get("id") or "").strip()
        title = (it.get("title") or "").strip() or "未命名"
        url = (it.get("url") or it.get("input_url") or "").strip()
        if not url:
            continue

        # ── 跨批次去重：检查 link_registry ──────────────────────────────
        if is_url_processed(url):
            cached = get_share_from_registry(url)
            log(f"[跨批次跳过] {title} (URL 已存在于 batch={cached.get('batch_id', '?')})")
            skipped_cross_batch.append({
                "id": item_id,
                "title": title,
                "name": title,
                "input_url": url,
                "share_url": cached.get("share_url", ""),
                "status": "skipped_cross_batch",
            })
            continue

        norm_items.append({"id": item_id, "title": title, "url": url})

    if not norm_items:
        log("[INFO] 所有 URL 均已处理过，生成空结果文件")
        out = {
            "batch_folder_name": "",
            "batch_folder_fid": "",
            "items": [],
            "share_results": skipped_cross_batch,
            "skipped_cross_batch": len(skipped_cross_batch),
        }
        Path(args.out_json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"[DONE] wrote {args.out_json} (all items were cross-batch duplicates)")
        return

    title_to_ids = {it["title"]: it.get("id", "") for it in norm_items}

    log(f"准备初始化 QuarkPanFileManager，待处理 {len(norm_items)} 条新链接，{len(skipped_cross_batch)} 条跨批次跳过")
    mgr = QuarkPanFileManager(headless=True, slow_mo=0)
    log("QuarkPanFileManager 初始化完成")

    # ── 固定目标目录，不再每次创建新批次文件夹 ─────────────────────────
    # 请在 .env 中设置 QUARK_TARGET_DIR_ID
    TARGET_DIR_ID = os.environ.get("QUARK_TARGET_DIR_ID", "")
    to_dir_id = TARGET_DIR_ID
    batch_id = f"fixed_{TARGET_DIR_ID}"  # 固定 batch_id 用于幂等状态追踪

    log(f"使用固定目标目录 fid={to_dir_id}")

    # ── 加载已有状态（支持中断恢复）────────────────────────────────────
    run_state = load_quark_run_state(batch_id)

    # ── 列出目标目录已有文件（转存前查重）───────────────────────────────
    log(f"正在列出目标目录已有文件...")
    existing_in_dir = await list_existing_files(mgr, to_dir_id)
    log(f"目标目录已有 {len(existing_in_dir)} 个文件")

    # ── 转存 + 生成分享链接 ────────────────────────────────────────────
    share_results: list = list(recover_share_results(batch_id))  # include already-done items
    processed_this_run = 0

    for idx, entry in enumerate(norm_items, 1):
        title = entry["title"]
        url = entry["url"]

        # ── per-item 幂等检查 ──────────────────────────────────────────
        if is_item_complete_for_batch(batch_id, title):
            log(f"[恢复] 跳过已完成项目：{title}")
            continue

        # 转存前查重（目标目录已有同名文件）
        if title in existing_in_dir:
            fid = existing_in_dir[title]
            log(f"[目录查重] 目标目录已存在同名文件 fid={fid}，跳过转存，直接生成分享链接：{title}")
        else:
            log(f"==== ({idx}/{len(norm_items)}) 开始转存：{title} ====")
            submit_result = await run_with_timeout(
                mgr.run(url, folder_id=to_dir_id, download=False),
                timeout_sec=args.transfer_timeout,
                stage=f"转存 {title}",
            )
            log(f"==== ({idx}/{len(norm_items)}) 转存完成：{title} ====")
            # 从 submit 响应中获取 fid：优先用 save_as.fid，否则用 save_as_top_fids[0]
            save_as = (submit_result or {}).get("data", {}).get("save_as", {}) if submit_result else {}
            fid = save_as.get("fid", "") or (save_as.get("save_as_top_fids") or [""])[0]
            if fid:
                log(f"[FID] 直接从 submit 响应获取 fid={fid}")
            else:
                # fallback：目录模糊匹配（Quark 可能自动给同名文件夹加 (1) 等后缀）
                await asyncio.sleep(3)
                existing_in_dir = await list_existing_files(mgr, to_dir_id)
                # 精确匹配
                fid = existing_in_dir.get(title)
                if not fid:
                    # 去除 (1), (2) ... 后缀后匹配
                    import re
                    base_title = re.sub(r'\(\d+\)', '', title).strip()
                    for fname, f in existing_in_dir.items():
                        normalized = re.sub(r'\(\d+\)', '', fname).strip()
                        if normalized == base_title:
                            fid = f
                            log(f"[FID] 模糊匹配：'{fname}' -> fid={fid}")
                            break
                if not fid:
                    # 最后手段：用任意第一个文件
                    fid = next(iter(existing_in_dir.values()), "")
                    log(f"[FID] fallback 取任意文件 fid={fid}")
                if not fid:
                    raise RuntimeError(f"转存完成后在目标目录找不到文件：{title}")

        # 标记已转存
        mark_transferred(batch_id, title, fid=fid, input_url=url)

        # 检查是否已生成分享链接（per-item 幂等）
        existing_share = run_state.get("shared", {}).get(title)
        if existing_share:
            share_url = existing_share["share_url"]
            share_id = existing_share["share_id"]
            log(f"[恢复] 跳过已生成分享：{title} -> {share_url}")
        else:
            log(f"开始生成分享：{title}（fid={fid}）")
            task_id = await run_with_timeout(
                mgr.get_share_task_id(fid, title, url_type=2, expired_type=1, password=''),
                timeout_sec=120,
                stage=f"创建分享任务 {title}",
            )
            share_id = await get_share_id_with_retry(mgr, task_id, timeout_sec=args.share_timeout)
            share_url, _ = await run_with_timeout(
                mgr.submit_share(share_id),
                timeout_sec=120,
                stage=f"提交分享 {title}",
            )
            log(f"[SHARE] {title} -> {share_url}")

        # 标记已分享 + 写入 link_registry（跨批次去重）
        mark_shared(batch_id, title, share_url=share_url, share_id=share_id, fid=fid)
        register_url(url, title, share_url, batch_id)

        share_results.append({
            "id": title_to_ids.get(title, ""),
            "title": title,
            "name": title,
            "fid": fid,
            "share_id": share_id,
            "share_url": share_url,
            "status": "ok",
        })
        processed_this_run += 1

    log(f"本轮处理完成，共处理 {processed_this_run} 条新记录")

    # ── 补全 skipped_cross_batch ───────────────────────────────────────
    for sc in skipped_cross_batch:
        share_results.append(sc)

    if not share_results:
        raise RuntimeError("批次目录里没有任何可分享的项目")

    out = {
        "batch_folder_name": f"固定目录_{TARGET_DIR_ID}",
        "batch_folder_fid": to_dir_id,
        "batch_id": batch_id,
        "items": [{"id": it["id"], "title": it["title"], "input_url": it["url"]} for it in norm_items],
        "share_results": share_results,
        "processed": processed_this_run,
        "skipped_cross_batch": len(skipped_cross_batch),
    }

    Path(args.out_json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[DONE] wrote {args.out_json}  (processed={processed_this_run}, skipped={len(skipped_cross_batch)})")


if __name__ == '__main__':
    asyncio.run(main())
