#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forward_to_publish.py

接收转发消息文本，一键完成：
  1. 解析消息 → {title, description, tags, quark_url, repo}
  2. 夸克网盘转存 + 生成分享链接
  3. Bing 图片搜索
  4. GitHub 发布（写 YYYYMM.md + commit + push）
  5. Bot API 注册 + Telegram 3群组推送

使用方式：
  python forward_to_publish.py --input-text "🎬 ..."
  python forward_to_publish.py --input-file /path/to/message.txt

触发：当用户发送包含"请注意"的消息时，
      OpenClaw agent 自动提取消息文本并调用本脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 本地模块 ─────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))

from _common import load_env_files, get_quark_root, get_mswnlz_root, work_dir_for_url, load_checkpoint, update_checkpoint_step, is_step_done, get_step_output
from image_search_adapter import fetch_images_for_item
from telegram_album_notify import send_album_message, send_text_message
import httpx

# 加载 QuarkPanTool 路径，使 quark_copy 可被导入
load_env_files()
QUARK_ROOT = get_quark_root(require=True)

# 源文件夹 FID（推广文件/子文件目录）
# 请在 .env 中设置 QUARK_PROMO_FOLDER_FID
SOURCE_FOLDER_FID = os.environ.get("QUARK_PROMO_FOLDER_FID", "")


def _copy_source_to_dest(dest_fid: str) -> bool:
    """
    使用 QuarkPanFileManager 列出源文件夹 + httpx 复制到 dest_fid。
    """
    # 延迟导入 QuarkPanFileManager（避免跨路径导入问题）
    sys.path.insert(0, str(QUARK_ROOT))
    from quark import QuarkPanFileManager

    mgr = QuarkPanFileManager(headless=True, slow_mo=0)

    # 用 mgr 的 headers 列出源文件夹内容
    src_data = asyncio.run(
        mgr.get_sorted_file_list(pdir_fid=SOURCE_FOLDER_FID, page='1', size='100', fetch_total='true')
    )
    src_items = src_data.get('data', {}).get('list', []) if src_data else []
    if not src_items:
        log(f"⚠ 源文件夹 {SOURCE_FOLDER_FID} 为空，跳过复制")
        return False

    src_fids = [item['fid'] for item in src_items]
    names = [item['file_name'] for item in src_items]
    log(f"  📋 源文件夹包含 {len(src_fids)} 项：{', '.join(names[:5])}{'...' if len(names) > 5 else ''}")

    # 用 mgr 的 headers 调用复制 API
    api = "https://drive-pc.quark.cn/1/clouddrive/file/copy"
    params = {
        'pr': 'ucpro',
        'fr': 'pc',
        '__dt': random.randint(100, 9999),
        '__t': int(time.time() * 1000),
    }
    data = {
        "action": "copy",
        "exclude_fids": [],
        "filelist": src_fids,
        "to_pdir_fid": dest_fid,
    }

    copy_ok = False
    try:
        import httpx
        resp = asyncio.run(
            httpx.AsyncClient().post(api, json=data, headers=mgr.headers, params=params, timeout=60)
        )
        result = resp.json()
        if result.get('status') == 200:
            task_id = result.get('data', {}).get('task_id')
            log(f"  ✅ 复制任务已创建，task_id={task_id}")
            copy_ok = True
        else:
            log(f"  ❌ 复制失败：{result.get('message')}")
    except Exception as exc:
        log(f"  ❌ 复制异常：{exc}")

    return copy_ok

# ── Bot API & TG 群组配置 ─────────────────────────────────────────────
BOT_API_URL = os.environ.get("BOT_API_URL", "").strip().rstrip("/")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_GROUPS = [
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

VALID_REPOS = (
    "AIknowledge",
    "auto",
    "book",
    "chinese-traditional",
    "cross-border",
    "curriculum",
    "edu-knowlege",
    "healthy",
    "movies",
    "self-media",
    "tools",
)

# ── 工具函数 ─────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[forward] {msg}", file=sys.stderr)


def escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def now_iso() -> str:
    import datetime as dt
    return dt.datetime.now().astimezone().isoformat()



def run_command(cmd: List[str], cwd: Optional[Path] = None, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, ensure_ascii=False, indent=2, fp=f)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ── 步骤 1：解析消息 ─────────────────────────────────────────────────

@dataclass
class ParsedItem:
    title: str
    description: str
    tags: List[str]
    quark_url: str
    repo: str


def parse_forward_message(text: str, repo: str) -> ParsedItem:
    """
    解析转发消息文本，提取：
      🎬 → title
      📝 → description
      🏷️ → tags（按空格拆分）
      🔗 → quark_url（正则提取 URL）
    """
    # 逐行处理，每行只匹配对应字段，避免跨行贪婪匹配
    lines = [ln for ln in text.splitlines()]

    title = ""
    description = ""
    tags: List[str] = []
    quark_url = ""

    for line in lines:
        original = line
        line = line.strip()
        if not line:
            continue

        # 🎬 标题
        if line.startswith("🎬"):
            content = line[len("🎬"):].strip()
            if not title and content:
                title = content

        # 📝 简介
        elif line.startswith("📝"):
            content = line[len("📝"):].strip()
            if not description and content:
                description = content

        # 🏷️ 标签（按空格拆分）
        elif line.startswith("🏷️"):
            content = line[len("🏷️"):].strip()
            if not tags and content:
                tags = [t for t in content.split(" ") if t.strip()]

        # 🔗 链接（全文搜索，支持跨行）
        elif "pan.quark.cn" in line:
            m = re.search(r"https?://pan\.quark\.cn/s/\S+", line)
            if m and not quark_url:
                quark_url = m.group(0).strip()

    # 校验必填字段
    if not title:
        raise ValueError("解析失败：未找到标题（🎬 行）")
    if not quark_url:
        raise ValueError("解析失败：未找到夸克链接（🔗 行）")

    log(f"解析完成：title={title[:30]}...")

    return ParsedItem(
        title=title,
        description=description,
        tags=tags,
        quark_url=quark_url,
        repo=repo,
    )


# ── 步骤 2：夸克转存 + 生成分享链接 ───────────────────────────────────

QUARK_BATCH_RUN = SCRIPT_DIR / "quark_batch_run.py"


def _recover_from_timeout(item: ParsedItem, work_dir: Path) -> Dict[str, str]:
    """
    quark_batch_run.py 超时后，从 Quark 网盘查询已转存文件的 fid，
    并直接生成永久分享链接。
    """
    import sys as _sys
    _sys.path.insert(0, str(SCRIPT_DIR))
    from _common import get_quark_root
    from quark import QuarkPanFileManager

    quark_root = get_quark_root(require=True)
    os.chdir(quark_root)

    mgr = QuarkPanFileManager(headless=True)

    # 遍历主保存目录，找到标题匹配的文件
    TARGET_DIR = os.environ.get("QUARK_TARGET_DIR_ID", "")
    page = 1
    size = 100
    matched_fid = None
    while True:
        data = mgr.get_sorted_file_list(pdir_fid=TARGET_DIR, page=str(page), size=str(size))
        lst = (data.get("data") or {}).get("list") or []
        for entry in lst:
            if entry.get("file_name") == item.title:
                matched_fid = entry.get("fid")
                break
        if matched_fid or not lst:
            break
        page += 1

    if not matched_fid:
        raise RuntimeError(
            f"_recover_from_timeout：未在夸克网盘找到文件“{item.title}”，" 
            f"请确认转存是否成功，手动重跑"
        )

    log(f"📦 超时恢复找到 fid={matched_fid}，正在生成分享链接...")

    # 生成永久分享链接
    task_id = mgr.get_share_task_id(matched_fid, item.title, url_type=2, expired_type=1, password="")
    share_id = None
    for _ in range(45):
        import time as _time
        _time.sleep(2)
        try:
            task_data = mgr.get_share_id(task_id)
            if task_data.get("data", {}).get("share_id"):
                share_id = task_data["data"]["share_id"]
                break
        except Exception:
            pass

    if not share_id:
        raise RuntimeError("_recover_from_timeout：等待 share_id 超时")

    share_url, _ = mgr.submit_share(share_id)
    log(f"🔗 超时恢复分享链接：{share_url[:60]}...")

    return {
        "share_url": share_url,
        "fid": matched_fid,
        "batch_folder_fid": TARGET_DIR,
    }


def quark_save_and_share(item: ParsedItem, work_dir: Path, label: str, month: str) -> Dict[str, str]:
    """
    调用 quark_batch_run.py：
      1. 转存夸克分享链接
      2. 生成永久加密分享链接
    返回真实的夸克分享链接（share_url）。
    """
    if not QUARK_BATCH_RUN.exists():
        raise FileNotFoundError(f"找不到 quark_batch_run.py：{QUARK_BATCH_RUN}")

    items_json = work_dir / "items.json"
    batch_json = work_dir / "batch_share_results.json"

    tags_str = "、".join(item.tags) if item.tags else ""

    items_payload = [
        {
            "title": item.title,
            "url": item.quark_url,
            "description": item.description,
            "tags": tags_str,
        }
    ]
    write_json(items_json, items_payload)

    cmd = [
        sys.executable,
        str(QUARK_BATCH_RUN),
        "--label", label,
        "--month", month,
        "--items-json", str(items_json),
        "--out-json", str(batch_json),
    ]

    log(f"执行夸克转存：{item.quark_url}")
    try:
        cp = run_command(cmd, cwd=SCRIPT_DIR, timeout=600)
    except subprocess.TimeoutExpired:
        log(f"⚠️ quark_batch_run.py 超时，尝试从夸克网盘恢复 fid...")
        return _recover_from_timeout(item, work_dir)

    if cp.returncode != 0:
        # 网络类错误（ConnectTimeout/ReadTimeout）意味着文件可能已保存，只是API调用失败
        stderr_lower = cp.stderr.lower()
        if any(x in stderr_lower for x in ["timeout", "connect", "network", "httpx", "httpcore"]):
            log(f"⚠️ quark_batch_run.py 网络错误，尝试从夸克网盘恢复 fid...")
            try:
                return _recover_from_timeout(item, work_dir)
            except Exception as rec_exc:
                log(f"恢复失败：{rec_exc}，回退到原错误")
        raise RuntimeError(f"quark_batch_run.py 失败\nSTDERR:\n{cp.stderr}\nSTDOUT:\n{cp.stdout[:500]}")

    try:
        batch_data = read_json(batch_json)
    except Exception as exc:
        raise RuntimeError(f"读取 batch_share_results.json 失败：{exc}") from exc

    share_results = batch_data.get("share_results") or []
    if not share_results:
        raise RuntimeError("quark_batch_run.py 未返回 share_results")

    # 从 share_results 中匹配当前 item 的标题（recover_share_results 包含同批次历史记录）
    current_result = None
    for sr in reversed(share_results):  # reversed: 优先取最新添加的
        if sr.get("title") == item.title:
            current_result = sr
            break
    if not current_result:
        current_result = share_results[-1]  # fallback: 取最后一条

    share_url = current_result.get("share_url") or ""
    if not share_url:
        raise RuntimeError("quark_batch_run.py 返回的 share_url 为空")

    log(f"夸克分享链接生成成功：{share_url[:60]}...")

    dest_fid = current_result.get("fid") or ""
    batch_folder_fid = batch_data.get("batch_folder_fid") or ""

    return {
        "share_url": share_url,
        "fid": dest_fid,
        "batch_folder_fid": batch_folder_fid,
    }


# ── 步骤 3：Bing 图片搜索 ─────────────────────────────────────────────

def search_images(item: ParsedItem, work_dir: Path) -> List[str]:
    """
    调用 image_search_adapter.py，用标题前20字作关键词抓图。
    返回图片路径列表。
    """
    keywords = [item.title[:20]] if item.title else []

    log(f"图片搜索关键词：{keywords}")

    try:
        result = fetch_images_for_item(
            item={"id": "0", "title": item.title},
            keywords=keywords,
            work_dir=work_dir,
            engine="bing",
            final_count=3,
            candidate_count=8,
            timeout_sec=90,
            min_width=400,
            min_height=400,
        )
    except Exception as exc:
        log(f"⚠ 图片搜索失败，降级无图模式：{exc}")
        return []

    files = result.get("files") or []
    log(f"图片搜索完成：{len(files)} 张有效图片")
    return files


# ── 步骤 4：GitHub 发布 ──────────────────────────────────────────────

MSWNLZ_PUBLISH = SCRIPT_DIR / "mswnlz_publish.py"


def github_publish(item: ParsedItem, repo: str, quark_url: str, share_url: str,
                   work_dir: Path, month: str) -> Dict[str, Any]:
    """
    复用 mswnlz_publish.py 的逻辑：
      - 写 {repo}/YYYYMM.md
      - git add + commit + push
    返回 publish 结果字典。
    """
    if not MSWNLZ_PUBLISH.exists():
        raise FileNotFoundError(f"找不到 mswnlz_publish.py：{MSWNLZ_PUBLISH}")

    batch_json = work_dir / "publish_batch.json"
    items_json = work_dir / "publish_items.json"
    result_json = work_dir / "publish_result.json"

    batch_payload = {
        "batch_folder_name": f"forward_{time.strftime('%Y%m%d')}",
        "batch_folder_fid": "forward",
        "share_results": [
            {
                "id": "0",
                "title": item.title,
                "name": item.title,
                "source_url": quark_url,
                "input_url": quark_url,
                "share_url": share_url,
                "status": "ok",
            }
        ],
    }
    write_json(batch_json, batch_payload)

    tags_str = "、".join(item.tags) if item.tags else ""
    publish_items = [
        {
            "id": "0",
            "title": item.title,
            "url": quark_url,
            "description": item.description,
            "tags": tags_str,
            "repo": repo,
        }
    ]
    write_json(items_json, {"items": publish_items})

    cmd = [
        sys.executable,
        str(MSWNLZ_PUBLISH),
        "--month", month,
        "--batch-json", str(batch_json),
        "--items-json", str(items_json),
        "--skip-telegram",
        "--result-json", str(result_json),
        "--emit-json",
    ]

    log(f"执行 GitHub 发布到仓库：{repo}")
    cp = run_command(cmd, cwd=SCRIPT_DIR, timeout=600)

    if cp.returncode != 0:
        log(f"⚠ mswnlz_publish.py 失败：{cp.stderr[:300]}")
        # 不抛出异常，继续执行后续步骤
        return {"status": "failed", "error": cp.stderr[:300]}

    try:
        result_data = read_json(result_json)
    except Exception as exc:
        log(f"⚠ 读取 publish_result.json 失败：{exc}")
        return {"status": "failed", "error": str(exc)}

    updated = result_data.get("updated_repos") or []
    log(f"GitHub 发布完成：{repo}，updated_repos={updated}")
    return {"status": "ok", "data": result_data}


# ── 步骤 5：Bot API 注册 ─────────────────────────────────────────────

def call_bot_api(item: ParsedItem, share_url: str) -> Optional[str]:
    """
    向 @GoodStudyDayUpBot 注册资源，获取 start_link。
    """
    if not BOT_API_URL:
        log("⚠ BOT_API_URL 未配置，跳过 Bot 注册")
        return None

    payload = {
        "resource_name": item.title[:80],
        "resource_description": item.description[:200] if item.description else "",
        "resource_link": share_url,
        "resource_hint": "",
    }

    log(f"Bot API 注册：{item.title[:40]}...")
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{BOT_API_URL}/api/add",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "forward_to_publish/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            start_link = result.get("start_link", "")
            if start_link:
                log(f"Bot 注册成功：{start_link[:60]}...")
                return start_link
            else:
                log(f"⚠ Bot API 未返回 start_link：{result}")
                return None
    except Exception as exc:
        log(f"⚠ Bot API 注册失败：{exc}")
        return None


# ── 步骤 5b：构建 caption ──────────────────────────────────────────────

def build_caption(item: ParsedItem, start_link: Optional[str]) -> str:
    """
    构建 Telegram 消息 caption。
    格式：
      <b>{title}</b> #{tag1} #{tag2} #{tag3}
      {description}

      💾 获取资源：👉 点我获取{title}👈（超链接 → start_link）
    """
    tags_part = ""
    if item.tags:
        tags_part = " " + " #".join([escape_html(t) for t in item.tags[:3]])

    title_escaped = escape_html(item.title)

    caption_parts = [f"<b>{title_escaped}</b>{tags_part}"]

    if item.description:
        caption_parts.append("")
        caption_parts.append(escape_html(item.description))

    if start_link:
        cta = f"💾 获取资源：<a href=\"{escape_html(start_link)}\">👉 点我获取{title_escaped}👈</a>"
    else:
        cta = f"🔗 夸克网盘：{escape_html(item.quark_url)}"

    caption_parts.extend(["", cta])

    caption = "\n".join(caption_parts)
    # Telegram caption 限制 1024 字符，caption 本身 900 字以内
    return caption[:900]


def _can_send_media(chat_id: str) -> bool:
    """
    检查 bot 在指定 chat 是否可以发送媒体消息。
    通过 getChat API 判断权限，返回 True = 可发 album，False = 只能 text。
    """
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChat?chat_id={chat_id}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get('ok'):
                perms = data['result'].get('permissions', {})
                return bool(perms.get('can_send_media_messages', True))
            return True  # 无法获取时默认尝试
    except Exception:
        return True  # 网络异常时默认尝试


# ── 步骤 5c：Telegram 推送 ───────────────────────────────────────────

def telegram_push(
    item: ParsedItem,
    images: List[str],
    caption: str,
) -> Dict[str, Any]:
    """
    遍历 3 个 TG 群组，分别发送 album 或 text。
    """
    if not TELEGRAM_BOT_TOKEN:
        log("⚠ TELEGRAM_BOT_TOKEN 未配置，跳过 TG 推送")
        return {"status": "skipped", "reason": "no_token"}

    results = []
    any_ok = False

    for group in TELEGRAM_GROUPS:
        chat_id = group.get("chat_id", "").strip()
        thread_id = group.get("thread_id")

        if not chat_id:
            continue

        # 检查该群是否允许发送媒体，无权限则降级 text
        can_media = _can_send_media(chat_id) if images else False

        try:
            if images and can_media:
                tg_result = send_album_message(
                    bot_token=TELEGRAM_BOT_TOKEN,
                    chat_id=chat_id,
                    image_files=images,
                    caption=caption,
                    message_thread_id=thread_id,
                    parse_mode="HTML",
                    disable_notification=False,
                )
            else:
                reason = "无图片" if not images else "无媒体权限（can_send_media_messages=false），降级为 text"
                log(f"⚠ {reason}")
                tg_result = send_text_message(
                    bot_token=TELEGRAM_BOT_TOKEN,
                    chat_id=chat_id,
                    text=caption,
                    message_thread_id=thread_id,
                    parse_mode="HTML",
                    disable_notification=False,
                )

            if tg_result.ok:
                log(f"TG 发送成功 → chat_id={chat_id}, message_id={tg_result.message_id}")
                any_ok = True
            else:
                log(f"TG 发送失败 → chat_id={chat_id}: {tg_result.error}")

            results.append({
                "chat_id": chat_id,
                "thread_id": thread_id,
                "ok": tg_result.ok,
                "message_id": tg_result.message_id,
                "error": tg_result.error,
            })
        except Exception as exc:
            log(f"TG 异常 → chat_id={chat_id}: {exc}")
            results.append({
                "chat_id": chat_id,
                "thread_id": thread_id,
                "ok": False,
                "error": str(exc),
            })

    if any_ok:
        # 清理本地图片
        for img_path in (images or []):
            try:
                Path(img_path).unlink(missing_ok=True)
                log(f"🗑 已删除本地图片：{img_path}")
            except Exception:
                pass

    return {
        "status": "ok" if any_ok else "failed",
        "groups": results,
    }


# ── 主流程 ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="转发消息 - 全自动发布")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input-text", help="直接传入消息文本")
    group.add_argument("--input-file", type=Path, help="从文件读取消息文本")
    parser.add_argument("--repo", required=True, help="目标仓库，如 tools/movies/book 等")
    parser.add_argument("--month", default=time.strftime("%Y%m"), help="资源月份，如 202604")
    parser.add_argument("--label", default="", help="批次标签（已废弃，仍可用但不影响幂等性）")
    parser.add_argument("--dry-run", action="store_true", help="仅解析，不执行转存/push/发送")
    args = parser.parse_args()

    # 读取消息文本
    if args.input_text:
        raw_text = args.input_text
    else:
        raw_text = args.input_file.read_text(encoding="utf-8")

    # ── 从 quark_url 计算幂等 work_dir ───────────────────────────
    # 先从 raw_text 提取 quark_url（不依赖 parse），作为 checkpoint 目录名
    quark_url_for_key = None
    for ln in raw_text.splitlines():
        m = re.search(r"https?://pan\.quark\.cn/s/\S+", ln)
        if m:
            quark_url_for_key = m.group(0).strip()
            break
    if not quark_url_for_key:
        log("❌ 无法从消息中提取 quark_url")
        return 1

    work_dir = work_dir_for_url(quark_url_for_key)
    run_id = quark_url_for_key   # run_id = quark_url，用于日志

    # ── 断点检测 ────────────────────────────────────────────────
    cp = load_checkpoint(work_dir)

    if cp:
        log(f"📦 检测到未完成的 run（quark_url={quark_url_for_key[:40]}...），从断点续跑")
        item = ParsedItem(
            title=cp["parsed"]["title"],
            description=cp["parsed"].get("description", ""),
            tags=cp["parsed"].get("tags", []),
            quark_url=cp["parsed"]["quark_url"],
            repo=cp["parsed"]["repo"],
        )
        args.repo = cp["parsed"]["repo"]
        parsed_dict = cp["parsed"]
        resumed = True
    else:
        log(f"🆕 新建 run（quark_url={quark_url_for_key[:40]}...）")
        item = None
        resumed = False

    # ── 步骤 0+1：解析（全新 run 才执行）────────────────────────
    if item is None:
        try:
            item = parse_forward_message(raw_text, args.repo)
        except ValueError as exc:
            log(f"❌ {exc}")
            return 1
        except Exception as exc:
            log(f"❌ 解析异常：{exc}")
            return 1
        parsed_dict = {
            "title": item.title,
            "description": item.description,
            "tags": item.tags,
            "quark_url": item.quark_url,
            "repo": args.repo,
        }
        update_checkpoint_step(work_dir, "parse", parsed_dict, parsed=parsed_dict)
        log(f"步骤0 AI 推理分类：repo={args.repo}")
        cp = load_checkpoint(work_dir)  # 重新加载，否则 cp 仍为 None
    else:
        log(f"⏭ 跳过步骤 parse（已执行）")

    result: Dict[str, Any] = {
        "run_id": run_id,
        "resumed": resumed,
        "month": args.month,
        "parsed": parsed_dict,
        "steps": {
            "parse":     {"status": "ok"},
            "quark_save":  {"status": "pending"},
            "quark_copy":  {"status": "pending"},
            "images":    {"status": "pending"},
            "publish":   {"status": "pending"},
            "telegram":  {"status": "pending"},
        },
        "started_at": now_iso(),
        "finished_at": None,
    }

    if args.dry_run:
        log("✅ [DRY-RUN] 解析成功，退出（dry-run 模式）")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.dry_run:
        log("✅ [DRY-RUN] 解析成功，退出（dry-run 模式）")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # ── 步骤 2：夸克转存（幂等）────────────────────────────────
    if is_step_done(cp, "quark_save"):
        log(f"⏭ 跳过 quark_save（已执行）")
        save_result = get_step_output(cp, "quark_save")
    else:
        try:
            save_result = quark_save_and_share(item, work_dir, args.label, args.month)
        except Exception as exc:
            log(f"❌ 步骤 2 失败：{exc}")
            result["steps"]["quark_save"] = {"status": "failed", "error": str(exc)}
            result["finished_at"] = now_iso()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        update_checkpoint_step(work_dir, "quark_save", save_result)

    real_share_url = save_result["share_url"]
    dest_fid = save_result["fid"]
    result["steps"]["quark_save"] = {"status": "ok", **save_result}

    # ── 步骤 2.5：复制推广文件（幂等）────────────────────────────
    if is_step_done(cp, "quark_copy"):
        log(f"⏭ 跳过 quark_copy（已执行）")
        copy_status = get_step_output(cp, "quark_copy")
    else:
        copy_status = "skipped"
        try:
            if dest_fid:
                log(f"📁 开始复制源文件夹内容到目标（fid={dest_fid}）...")
                ok = _copy_source_to_dest(dest_fid)
                copy_status = "ok" if ok else "failed"
            else:
                log(f"📄 无目标 FID，跳过复制步骤")
        except Exception as exc:
            log(f"⚠ 步骤 2.5 复制失败：{exc}")
            copy_status = "failed"
        update_checkpoint_step(work_dir, "quark_copy", copy_status)
    result["steps"]["quark_copy"] = {"status": copy_status}

    # ── 步骤 3：图片搜索（幂等）────────────────────────────────
    if is_step_done(cp, "images"):
        log(f"⏭ 跳过 images（已执行）")
        images = get_step_output(cp, "images") or []
    else:
        images = []
        try:
            images = search_images(item, work_dir)
        except Exception as exc:
            log(f"⚠ 图片搜索异常：{exc}")
        update_checkpoint_step(work_dir, "images", images)
    result["steps"]["images"] = {"status": "ok" if images else "partial", "files": images}

    # ── 步骤 4：GitHub 发布（幂等）───────────────────────────────
    if is_step_done(cp, "publish"):
        log(f"⏭ 跳过 publish（已执行）")
        publish_result = get_step_output(cp, "publish")
    else:
        try:
            publish_result = github_publish(item, args.repo, item.quark_url, real_share_url,
                                             work_dir, args.month)
        except Exception as exc:
            log(f"⚠ GitHub 发布异常：{exc}")
            publish_result = {"status": "failed", "error": str(exc)}
        update_checkpoint_step(work_dir, "publish", publish_result)
    result["steps"]["publish"] = publish_result

    # ── 步骤 5：Bot 注册 + TG 推送（幂等）───────────────────────
    if is_step_done(cp, "telegram"):
        log(f"⏭ 跳过 telegram（已执行）")
        tg_result = get_step_output(cp, "telegram")
        start_link = None
    else:
        start_link = None
        try:
            start_link = call_bot_api(item, real_share_url)
        except Exception as exc:
            log(f"⚠ Bot 注册异常：{exc}")
        try:
            caption = build_caption(item, start_link)
            tg_result = telegram_push(item, images, caption)
        except Exception as exc:
            log(f"⚠ Telegram 推送异常：{exc}")
            tg_result = {"status": "failed", "error": str(exc)}
        update_checkpoint_step(work_dir, "telegram", tg_result)
    result["steps"]["telegram"] = tg_result

    # ── 标记完成 ────────────────────────────────────────────────
    from _common import save_checkpoint
    save_checkpoint(
        work_dir,
        completed_steps=["parse", "quark_save", "quark_copy", "images", "publish", "telegram"],
        step_outputs={}, parsed=parsed_dict, status="completed"
    )

    result["finished_at"] = now_iso()

    log(f"\n✅ 完成！ run_id={run_id} | repo={args.repo} | "
        f"quark={result['steps']['quark_save']['status']} | "
        f"copy={result['steps']['quark_copy']['status']} | "
        f"images={result['steps']['images']['status']} | "
        f"publish={result['steps']['publish'].get('status')} | "
        f"telegram={result['steps']['telegram'].get('status')}")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"❌ 未捕获异常：{exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
