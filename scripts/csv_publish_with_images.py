#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 title+share_url.csv 读取资源，依次完成：
  1. Bing 图片搜索抓图（间隔 1-2 分钟随机延迟）
  2. GitHub 内容仓库发布（调用 mswnlz_publish.py，commit + push，跳过 Telegram）
  3. Telegram 图文推送（每条资源发送一个 Album，支持 HTML Caption）

输入文件格式（title+share_url.csv）：
    标题,完整链接
    2025年杂志合集,https://pan.quark.cn/s/xxx?pwd=xxxx
    黑神话悟空 4K,https://pan.quark.cn/s/yyy?pwd=zzzz

使用方式：
    python csv_publish_with_images.py \
        --csv /root/.openclaw/workspace/title+share_url.csv \
        --month $(date +%Y%m) \
        --out result.json \
        [--skip-push] \
        [--skip-rebuild] \
        [--dry-run] \
        [--image-count 3] \
        [--notify-mode album]   # album | text | off
"""

from __future__ import annotations

import argparse
import csv
import copy
import json
import os
import random
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Bot API 配置 ───────────────────────────────────────────────────────
BOT_API_URL = os.environ.get("BOT_API_URL", "").strip().rstrip("/")

# ── 本地模块 ──────────────────────────────────────────────────────────
from _common import get_mswnlz_root, load_env_files
from image_search_adapter import fetch_images_for_item
from mswnlz_publish import generate_resource_summary, generate_resource_tags
from telegram_album_notify import send_album_message, send_text_message
from title_keyword_utils import build_keywords_for_item

load_env_files()

# 强制覆盖为正确的群组配置（.env 中 setdefault 无法覆盖已有环境变量）
os.environ.setdefault("TG_GROUP_1_ID", "-1003307944012")
os.environ.setdefault("TG_GROUP_1_THREAD", "126")

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = SKILL_ROOT.parent.parent
PUBLISH_SCRIPT = SCRIPT_DIR / "mswnlz_publish.py"

# 固定最小/最大延迟（秒）
MIN_DELAY = 1    # 1 秒
MAX_DELAY = 5     # 5 秒


@dataclass
class RunOptions:
    csv_path: Path
    month: str
    out_path: Path
    work_dir: Path
    label: Optional[str] = None
    skip_push: bool = False
    skip_rebuild: bool = False
    dry_run: bool = False
    image_count: int = 3
    image_candidates: int = 8
    image_timeout: int = 90
    image_engine: str = "bing"
    telegram_chat_id: Optional[str] = field(default=None)
    telegram_thread_id: Optional[str] = field(default=None)
    notify_mode: str = "album"      # album | text | off
    caption_template: Optional[Path] = None
    continue_on_image_error: bool = True
    continue_on_telegram_error: bool = True
    fail_fast: bool = False


# ── 工具函数 ──────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[csv-pub] {msg}", file=sys.stderr)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, ensure_ascii=False, indent=2, fp=f)


def now_iso() -> str:
    import datetime as dt
    return dt.datetime.now().astimezone().isoformat()


def make_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run_command(cmd: List[str], cwd: Optional[Path] = None, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout, check=False)


# ── CSV 读取 ──────────────────────────────────────────────────────────

def read_csv_items(csv_path: Path) -> List[Dict[str, str]]:
    """
    读取 title+share_url.csv，返回:
        [{"id": "0", "title": "...", "share_url": "..."}, ...]
    支持 "标题,完整链接" 和 "标题,链接" 两种表头。
    """
    items = []
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            # 兼容两种列名
            title = (row.get("标题") or row.get("标题 ") or "").strip()
            url = (row.get("完整链接") or row.get("链接") or
                   row.get("url") or row.get("URL") or "").strip()
            if title and url:
                items.append({"id": str(idx), "title": title, "share_url": url})
    return items


# ── 图片搜索 ──────────────────────────────────────────────────────────

def search_images_for_item(item: Dict[str, Any], options: RunOptions,
                          work_dir: Path) -> Dict[str, Any]:
    """对单条资源抓图，返回图片文件路径列表。"""
    keywords = build_keywords_for_item(item)
    try:
        result = fetch_images_for_item(
            item=item,
            keywords=keywords,
            work_dir=work_dir,
            engine=options.image_engine,
            final_count=options.image_count,
            candidate_count=options.image_candidates,
            timeout_sec=options.image_timeout,
            min_width=400,
            min_height=400,
        )
    except Exception as exc:
        log(f"  ⚠ 图片抓取失败: {exc}")
        if options.continue_on_image_error:
            return {"status": "failed", "files": [], "error": str(exc)}
        if options.fail_fast:
            raise
        return {"status": "failed", "files": [], "error": str(exc)}
    return result


# ── 资源摘要 & Caption ────────────────────────────────────────────────

def load_caption_template(path: Optional[Path]) -> Optional[str]:
    if path and path.exists():
        return path.read_text(encoding="utf-8")
    return None


def build_caption(item: Dict[str, Any], item_result: Dict[str, Any],
                  template_text: Optional[str]) -> str:
    title = escape_html(item["title"])
    # 优先用 CSV 原始 share_url，其次用 mswnlz_publish 返回的（可能是中转后的）
    raw_url = item.get("share_url") or item_result.get("publish", {}).get("share_url") or ""
    share_url = escape_html(raw_url) if raw_url else "（暂无）"
    repo = str(item_result.get("publish", {}).get("repo") or "")
    summary = escape_html(generate_resource_summary(item["title"], repo))
    tags = [escape_html(t) for t in generate_resource_tags(item["title"], repo)[:3]]
    tags_line = "🏷 资源标签：" + " ".join(tags) if tags else ""
    extra_line = escape_html(item.get("telegram_caption_extra", "")).strip()

    if template_text:
        caption = template_text.format(
            title=title,
            share_url=share_url,
            summary=summary,
            tags_line=tags_line,
            extra_line=extra_line,
        )
    else:
        lines = [
            f"<b>{title}</b>",
            "",
            f"📝 资源简介：{summary}",
            tags_line,
            f"🔗 夸克网盘：{share_url}",
        ]
        if extra_line:
            lines.extend(["", extra_line])
        caption = "\n".join(line for line in lines if line)

    return caption[:900]


# ── Telegram 发送 ────────────────────────────────────────────────────

def can_send_album(item_result: Dict[str, Any], options: RunOptions) -> bool:
    if options.notify_mode != "album":
        return False
    return bool(item_result.get("images", {}).get("files"))


def _cleanup_images(item_result: Dict[str, Any]) -> None:
    """发送成功后删除本地图片文件，释放磁盘空间。"""
    files = item_result.get("images", {}).get("files", [])
    if not files:
        return
    deleted = 0
    for path_str in files:
        try:
            p = Path(path_str)
            if p.exists():
                p.unlink()
                deleted += 1
        except Exception as exc:
            log(f"  ⚠ 删除图片失败 {path_str}: {exc}")
    if deleted:
        log(f"  🗑 已删除 {deleted} 张本地图片，释放磁盘空间")


def call_bot_api(item: Dict[str, Any], item_result: Dict[str, Any]) -> Optional[str]:
    """调用 GoodStudyDayUpBot /api/add 注册资源，返回 start_link 或 None。"""
    quark_url = item.get("share_url") or item_result.get("quark_share_url", "")
    if not quark_url:
        return None
    title = item.get("title", "")
    resource_name = title[:80]
    import re as _re
    # 摘要取 telegram_caption 或生成摘要
    summary = item_result.get("telegram_caption", "") or ""
    # 去掉 HTML 标签和前缀标签，只留正文
    summary = _re.sub(r'<[^>]+>', '', summary).strip()
    summary = _re.sub(r'^(📝资源简介：|📝|🔗夸克网盘：|🔗|🏷资源标签：|🏷|\*+)', '', summary).strip()

    payload = json.dumps({
        "resource_name": resource_name,
        "resource_description": summary[:200],
        "resource_link": quark_url,
        "resource_hint": ""
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{BOT_API_URL}/api/add",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "mswnlz-publisher/1.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            start_link = result.get("start_link", "")
            if start_link:
                log(f"  🤖 Bot 注册成功，ID: {result.get('id')}")
                return start_link
    except Exception as exc:
        log(f"  ⚠ Bot API 调用失败: {exc}")
    return None


def send_telegram_for_item(item: Dict[str, Any], item_result: Dict[str, Any],
                            options: RunOptions) -> None:
    if options.notify_mode == "off":
        item_result["telegram"] = {"status": "skipped", "reason": "notify_mode=off"}
        return

    caption = item_result.get("telegram_caption", "")
    if not caption:
        caption = build_caption(item, item_result, options.caption_template)
    item_result["telegram_caption"] = caption

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # 读取所有已配置的 TG 群组
    all_groups = [
        {
            "chat_id": os.environ.get("TG_GROUP_1_ID", "").strip(),
            "thread_id": (os.environ.get("TG_GROUP_1_THREAD", "") or "").strip() or None,
        },
        {
            "chat_id": os.environ.get("TG_GROUP_2_ID", "").strip(),
            "thread_id": (os.environ.get("TG_GROUP_2_THREAD", "") or "").strip() or None,
        },
        {
            "chat_id": os.environ.get("TG_GROUP_3_ID", "").strip(),
            "thread_id": (os.environ.get("TG_GROUP_3_THREAD", "") or "").strip() or None,
        },
    ]
    # 过滤掉空 chat_id
    groups = [g for g in all_groups if g["chat_id"]]

    sent_ok = []
    sent_failed = []
    for g in groups:
        chat_id = g["chat_id"]
        thread_id = g["thread_id"]
        try:
            if can_send_album(item_result, options):
                tg_result = send_album_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    image_files=item_result["images"].get("files", []),
                    caption=caption,
                    message_thread_id=thread_id,
                    parse_mode="HTML",
                    disable_notification=False,
                )
                ok = tg_result.ok
            else:
                tg_result = send_text_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=caption,
                    message_thread_id=thread_id,
                    parse_mode="HTML",
                    disable_notification=False,
                )
                ok = tg_result.ok
            if ok:
                sent_ok.append(chat_id)
            else:
                sent_failed.append({"chat_id": chat_id, "error": tg_result.to_dict()})
        except Exception as exc:
            log(f"  ⚠ Telegram 发送失败 [{chat_id}]: {exc}")
            sent_failed.append({"chat_id": chat_id, "error": str(exc)})
            if options.fail_fast:
                raise

    # 所有群组发送完成后统一清理图片（不论成功多少个，只清理一次）
    if sent_ok or sent_failed:
        _cleanup_images(item_result)

    if sent_ok and not sent_failed:
        item_result["telegram"] = {"status": "ok", "groups": sent_ok}
    elif sent_ok and sent_failed:
        item_result["telegram"] = {"status": "partial", "ok": sent_ok, "failed": sent_failed}
    elif sent_failed:
        item_result["telegram"] = {"status": "failed", "groups": sent_failed}
    else:
        item_result["telegram"] = {"status": "skipped", "reason": "no_telegram_groups"}


# ── GitHub 发布 ──────────────────────────────────────────────────────

def run_publish(month: str, batch_json: Path, items_json: Path,
                result_json: Path, skip_push: bool, skip_rebuild: bool,
                dry_run: bool) -> Dict[str, Any]:
    if dry_run:
        items = read_csv_items(items_json)
        return {
            "month": month,
            "items": [
                {
                    "id": f"item_{i}",
                    "title": it["title"],
                    "repo": "curriculum",
                    "repo_path": f"curriculum/{month}.md",
                    "content_url": "https://openaitx.github.io/view.html?user=YOUR_GITHUB_USERNAME&project=curriculum&lang=zh-CN",
                    "share_url": it["share_url"],
                    "status": "published",
                }
                for i, it in enumerate(items)
            ],
            "commit": {"created": False, "pushed": False},
            "rebuild": {"triggered": False},
        }

    cmd = [
        sys.executable, str(PUBLISH_SCRIPT),
        "--month", month,
        "--batch-json", str(batch_json),
        "--items-json", str(items_json),
        "--skip-telegram",
        "--result-json", str(result_json),
        "--emit-json",
    ]
    if skip_push:
        cmd.append("--skip-push")
    if skip_rebuild:
        cmd.append("--skip-rebuild")

    cp = run_command(cmd, cwd=SCRIPT_DIR)
    if cp.returncode != 0:
        raise RuntimeError(f"mswnlz_publish.py 失败\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
    return read_json(result_json)


def attach_publish_results(csv_items: List[Dict[str, Any]],
                           publish_result: Dict[str, Any],
                           result: Dict[str, Any]) -> None:
    published = publish_result.get("items") or []
    by_title = {str(it["title"]): it for it in published}
    for idx, item in enumerate(csv_items):
        matched = by_title.get(item["title"], {})
        # mswnlz_publish.py 返回的 share_url 才是真正的夸克网盘分享链接
        quark_share_url = matched.get("share_url") or item.get("share_url", "")
        result["items"][idx]["publish"] = {
            "status": matched.get("status", "published"),
            "repo": matched.get("repo", "curriculum"),
            "repo_path": matched.get("repo_path"),
            "content_url": matched.get("content_url"),
            "share_url": quark_share_url,  # caption 读这个key，显示夸克链接
        }
        result["items"][idx]["quark_share_url"] = quark_share_url


# ── 主流程 ──────────────────────────────────────────────────────────

def sleep_random(min_sec: int = MIN_DELAY, max_sec: int = MAX_DELAY) -> None:
    t = random.randint(min_sec, max_sec)
    log(f"  ⏳ 随机等待 {t} 秒...")
    time.sleep(t)


def init_result(run_id: str, options: RunOptions,
                items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "month": options.month,
        "status": "running",
        "started_at": now_iso(),
        "finished_at": None,
        "items": [
            {
                "id": item.get("id") or f"item_{idx}",
                "title": item["title"],
                "share_url": item["share_url"],
                "quark": {"status": "skipped"},
                "publish": {"status": "pending"},
                "images": {"status": "pending"},
                "telegram": {"status": "pending"},
                "errors": [],
            }
            for idx, item in enumerate(items)
        ],
        "summary": {
            "total": len(items),
            "published_ok": 0,
            "image_ok": 0,
            "telegram_ok": 0,
            "partial": 0,
            "failed": 0,
        },
    }


def finalize_result(result: Dict[str, Any]) -> None:
    result["finished_at"] = now_iso()
    for item_result in result["items"]:
        if item_result.get("publish", {}).get("status") in {"published", "ok"}:
            result["summary"]["published_ok"] += 1
        if item_result.get("images", {}).get("status") == "ok":
            result["summary"]["image_ok"] += 1
        if item_result.get("telegram", {}).get("status") == "ok":
            result["summary"]["telegram_ok"] += 1
        statuses = [
            item_result.get("publish", {}).get("status"),
            item_result.get("images", {}).get("status"),
            item_result.get("telegram", {}).get("status"),
        ]
        if "failed" in statuses:
            result["summary"]["failed"] += 1
        elif "partial" in statuses:
            result["summary"]["partial"] += 1

    if result["summary"]["failed"] == 0 and result["summary"]["partial"] == 0:
        result["status"] = "ok"
    elif result["summary"]["failed"] == 0:
        result["status"] = "partial_success"
    else:
        result["status"] = "failed"


# ── 参数解析 ─────────────────────────────────────────────────────────

def parse_args() -> RunOptions:
    ap = argparse.ArgumentParser(
        description="读取 title+share_url.csv，依次完成抓图→发布→Telegram推送"
    )
    ap.add_argument("--csv", required=True, help="title+share_url.csv 路径")
    ap.add_argument("--month", required=False, default=time.strftime("%Y%m"),
                    help=f"资源月份，如 202604（默认自动取当前年月：{time.strftime('%Y%m')}）")
    ap.add_argument("--out", required=True, dest="out_path",
                    help="输出结果 JSON 路径")
    ap.add_argument("--work-dir", default="./tmp_csv_publish")
    ap.add_argument("--label")
    ap.add_argument("--skip-push", action="store_true")
    ap.add_argument("--skip-rebuild", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--image-count", type=int, default=3)
    ap.add_argument("--image-candidates", type=int, default=8)
    ap.add_argument("--image-timeout", type=int, default=90)
    ap.add_argument("--image-engine", default="bing")
    ap.add_argument("--telegram-chat-id")
    ap.add_argument("--telegram-thread-id")
    ap.add_argument("--notify-mode", default="album",
                    choices=["album", "text", "off"])
    ap.add_argument("--caption-template")
    ap.add_argument("--continue-on-image-error", action="store_true", default=True)
    ap.add_argument("--continue-on-telegram-error", action="store_true", default=True)
    ap.add_argument("--fail-fast", action="store_true")
    ns = ap.parse_args()
    return RunOptions(
        csv_path=Path(ns.csv),
        month=ns.month,
        out_path=Path(ns.out_path),
        work_dir=Path(ns.work_dir),
        label=ns.label,
        skip_push=ns.skip_push,
        skip_rebuild=ns.skip_rebuild,
        dry_run=ns.dry_run,
        image_count=ns.image_count,
        image_candidates=ns.image_candidates,
        image_timeout=ns.image_timeout,
        image_engine=ns.image_engine,
        telegram_chat_id=ns.telegram_chat_id,
        telegram_thread_id=ns.telegram_thread_id,
        notify_mode=ns.notify_mode,
        caption_template=Path(ns.caption_template) if ns.caption_template else None,
        continue_on_image_error=ns.continue_on_image_error,
        continue_on_telegram_error=ns.continue_on_telegram_error,
        fail_fast=ns.fail_fast,
    )


# ── 主入口 ──────────────────────────────────────────────────────────

def main() -> int:
    options = parse_args()

    # 0. 预加载 caption 模板
    template_text = load_caption_template(options.caption_template) if options.caption_template else None

    # 1. 读取 CSV
    if not options.csv_path.exists():
        log(f"❌ CSV 文件不存在: {options.csv_path}")
        return 1
    csv_items = read_csv_items(options.csv_path)
    if not csv_items:
        log("❌ CSV 为空或无可用数据行")
        return 1
    log(f"📋 读取到 {len(csv_items)} 条资源")

    # 2. 初始化结果结构
    run_id = make_run_id()
    work_dir = (options.work_dir / run_id).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    result = init_result(run_id, options, csv_items)

    # 3. 逐条处理：随机延迟 → 抓图 → 更新结果
    for idx, item in enumerate(csv_items):
        item_id = item.get("id") or f"item_{idx}"
        log(f"\n[{idx+1}/{len(csv_items)}] 处理: {item['title'][:50]}")

        # 随机延迟（每条之间 1-2 分钟）
        if idx > 0:   # 第一条不等待
            sleep_random()

        # 3a. 图片搜索
        item_result = result["items"][idx]
        try:
            img_result = search_images_for_item(item, options, work_dir)
            item_result["images"] = img_result
        except Exception as exc:
            item_result["images"] = {"status": "failed", "files": [], "error": str(exc)}
            item_result["errors"].append({"stage": "images", "message": str(exc)})
            if options.fail_fast:
                raise

        # 3b. 立即构建 caption（publish 结果稍后注入）
        caption = build_caption(item, item_result, template_text)
        item_result["telegram_caption"] = caption

        log(f"  ✅ 图片: {len(item_result['images'].get('files', []))} 张，状态: {item_result['images'].get('status')}")

    # 4. 批量发布到 GitHub
    log(f"\n📦 批量发布 {len(csv_items)} 条资源到 GitHub...")
    batch_json = work_dir / "batch_share_results.json"
    items_json = work_dir / "items_for_publish.json"
    result_json = work_dir / "publish_result.json"

    # 构造 batch JSON（模拟 quark_batch_run 格式）
    current_month = time.strftime("%Y%m")  # 修复：资源文件应以月份命名，而非 CSV 文件名
    batch_payload = {
        "batch_folder_name": f"csv_batch_{current_month}",
        "batch_folder_fid": "csv",
        "share_results": [
            {
                "id": item.get("id") or f"item_{idx}",
                "title": item["title"],
                "name": item["title"],
                "source_url": item["share_url"],
                "input_url": item["share_url"],
                "share_url": item["share_url"],
                "status": "ok",
            }
            for idx, item in enumerate(csv_items)
        ],
    }
    write_json(batch_json, batch_payload)
    write_json(items_json, {"items": csv_items})

    try:
        publish_result = run_publish(
            current_month, batch_json, items_json, result_json,
            skip_push=options.skip_push,
            skip_rebuild=options.skip_rebuild,
            dry_run=options.dry_run,
        )
    except Exception as exc:
        log(f"❌ mswnlz_publish.py 调用失败: {exc}")
        result["status"] = "failed"
        result["fatal_error"] = str(exc)
        write_json(options.out_path, result)
        return 1

    # 注入 publish 结果
    attach_publish_results(csv_items, publish_result, result)
    log(f"  ✅ GitHub 发布完成，updated_repos: {publish_result.get('updated_repos')}")

    # 5. Telegram 推送（逐条）
    if options.notify_mode != "off":
        log(f"\n📨 开始 Telegram 推送（{len(csv_items)} 条）...")
        for idx, item in enumerate(csv_items):
            item_result = result["items"][idx]
            log(f"  [{idx+1}/{len(csv_items)}] {item['title'][:40]}")

            # 调用 Bot API 注册资源，获取 start_link
            start_link = call_bot_api(item, item_result)

            quark_url = item.get("share_url") or item_result.get("quark_share_url", "") or ""
            repo = str(item_result.get("publish", {}).get("repo") or "")
            tags = [escape_html(t) for t in generate_resource_tags(item["title"], repo)[:3]]
            # 标题直接用原始名称
            clean_name = item["title"].strip()

            tags_line = " ".join(tags) if tags else ""

            if start_link:
                # 格式：{title} #{tag1} #{tag2} #{tag3} {summary单段}\n\n💾 获取资源：👉 点我获取{title}👈（整段为超链接）
                tags_inline = (" " + " ".join(tags)) if tags else ""
                summary = escape_html(generate_resource_summary(item["title"], repo))
                first_line = f"{clean_name}{tags_inline} {summary}"
                cta_line = (f"💾 获取资源：👉 <a href=\"{start_link}\">"
                             f"点我获取{clean_name}👈</a>")
                caption = f"{first_line}\n\n{cta_line}"
                caption = caption[:900]
            else:
                # 降级：旧格式（Bot API 失败时）
                caption = build_caption(item, item_result, options.caption_template)

            item_result["telegram_caption"] = caption
            send_telegram_for_item(item, item_result, options)
            tg_status = item_result.get("telegram", {}).get("status", "unknown")
            log(f"    Telegram: {tg_status}")

    # 6. 最终汇总
    finalize_result(result)
    write_json(options.out_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    log(f"\n✅ 完成！ total={result['summary']['total']} "
        f"published_ok={result['summary']['published_ok']} "
        f"image_ok={result['summary']['image_ok']} "
        f"telegram_ok={result['summary']['telegram_ok']} "
        f"failed={result['summary']['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
