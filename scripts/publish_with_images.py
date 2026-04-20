#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import load_env_files
from image_search_adapter import fetch_images_for_item
from mswnlz_publish import generate_resource_summary, generate_resource_tags
from telegram_album_notify import send_album_message, send_text_message
from title_keyword_utils import build_keywords_for_item

load_env_files()

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = SKILL_ROOT.parent.parent
QUARK_SCRIPT = SCRIPT_DIR / "quark_batch_run.py"
PUBLISH_SCRIPT = SCRIPT_DIR / "mswnlz_publish.py"


@dataclass
class RunOptions:
    month: str
    items_json: Path
    result_json: Path
    work_dir: Path
    label: Optional[str] = None
    skip_quark: bool = False
    batch_json: Optional[Path] = None
    skip_publish: bool = False
    skip_push: bool = False
    skip_rebuild: bool = False
    dry_run: bool = False
    image_engine: str = "bing"
    image_count: Optional[int] = None
    image_candidates: Optional[int] = None
    image_timeout: Optional[int] = None
    telegram_chat_id: Optional[str] = None
    telegram_thread_id: Optional[str] = None
    notify_mode: str = "album"
    caption_template: Optional[Path] = None
    send_summary: bool = False
    continue_on_image_error: bool = True
    continue_on_telegram_error: bool = True
    fail_fast: bool = False


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def now_iso() -> str:
    import datetime as dt
    return dt.datetime.now().astimezone().isoformat()


def make_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def normalize_items(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    defaults = cfg.get("defaults", {}) or {}
    merged_items: List[Dict[str, Any]] = []
    seen_ids = set()
    for idx, item in enumerate(cfg["items"], start=1):
        merged = deep_merge(defaults, item)
        merged.setdefault("image", {})
        merged.setdefault("telegram", {})
        merged.setdefault("publish", {})
        if not merged.get("id"):
            merged["id"] = f"item_{idx:03d}"
        if merged["id"] in seen_ids:
            raise ValueError(f"重复的 item.id: {merged['id']}")
        seen_ids.add(merged["id"])
        merged_items.append(merged)
    return merged_items


def validate_config(cfg: Dict[str, Any]) -> None:
    if "items" not in cfg or not isinstance(cfg["items"], list) or not cfg["items"]:
        raise ValueError("items.json 缺少 items 或 items 为空")
    for idx, item in enumerate(cfg["items"], start=1):
        if not item.get("title"):
            raise ValueError(f"第 {idx} 条资源缺少 title")
        if not item.get("url"):
            raise ValueError(f"第 {idx} 条资源缺少 url")
        if not str(item["url"]).startswith("https://pan.quark.cn/"):
            raise ValueError(f"第 {idx} 条资源 url 不是有效的夸克链接: {item['url']}")


def init_result(run_id: str, options: RunOptions, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "month": options.month,
        "status": "running",
        "started_at": now_iso(),
        "finished_at": None,
        "items": [
            {
                "id": item["id"],
                "title": item["title"],
                "quark": {"status": "pending"},
                "publish": {"status": "pending"},
                "images": {"status": "pending"},
                "telegram": {"status": "pending"},
                "errors": [],
            }
            for item in items
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


def get_item_result(result: Dict[str, Any], item_id: str) -> Dict[str, Any]:
    for entry in result["items"]:
        if entry["id"] == item_id:
            return entry
    raise KeyError(f"找不到 item result: {item_id}")


def append_error(item_result: Dict[str, Any], stage: str, message: str) -> None:
    item_result["errors"].append({"stage": stage, "message": message})


def escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_caption_template(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    return path.read_text(encoding="utf-8")


def build_caption(item: Dict[str, Any], item_result: Dict[str, Any], template_text: Optional[str]) -> str:
    title = escape_html(item["title"])
    share_url = escape_html(item_result.get("quark", {}).get("share_url") or "（暂无）")
    repo = str(item_result.get("publish", {}).get("repo") or item.get("repo") or "")
    summary = escape_html(generate_resource_summary(item["title"], repo))
    tags = [escape_html(tag) for tag in generate_resource_tags(item["title"], repo)[:3]]
    tags_line = "🏷 资源标签：" + " ".join(tags) if tags else ""
    extra_line = escape_html(item.get("telegram", {}).get("caption_extra", "")).strip()

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
        caption = "\n".join(line for line in lines if line != "")
    return caption[:900]


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


def run_quark_batch(items: List[Dict[str, Any]], month: str, label: Optional[str], out_json: Path, dry_run: bool = False) -> Dict[str, Any]:
    if dry_run:
        return {
            "batch_folder_name": "dry_run_batch",
            "batch_folder_fid": "dry-run",
            "items": [{"id": item["id"], "title": item["title"], "input_url": item["url"]} for item in items],
            "share_results": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "name": item["title"],
                    "source_url": item["url"],
                    "share_url": f"https://pan.quark.cn/s/mock_{item['id']}",
                    "status": "ok",
                }
                for item in items
            ],
        }

    input_json = out_json.parent / "items_for_quark.json"
    payload = [{"id": item["id"], "title": item["title"], "url": item["url"]} for item in items]
    write_json(input_json, payload)
    cmd = [
        sys.executable,
        str(QUARK_SCRIPT),
        "--month",
        month,
        "--items-json",
        str(input_json),
        "--out-json",
        str(out_json),
    ]
    if label:
        cmd.extend(["--label", label])
    cp = run_command(cmd, cwd=SCRIPT_DIR, timeout=None)
    if cp.returncode != 0:
        raise RuntimeError(f"quark_batch_run.py 失败\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
    return read_json(out_json)


def run_publish(month: str, batch_json: Path, result_json: Path, items_json: Path, skip_push: bool = False, skip_rebuild: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    if dry_run:
        batch = read_json(batch_json)
        share_results = batch.get("share_results", [])
        item_cfg = read_json(items_json)
        hint_items = item_cfg.get("items") if isinstance(item_cfg, dict) else item_cfg
        hint_by_title = {
            str((entry.get("title") or entry.get("name") or "").strip()): entry
            for entry in (hint_items or [])
            if isinstance(entry, dict) and (entry.get("title") or entry.get("name"))
        }
        return {
            "month": month,
            "items": [
                {
                    "id": (hint_by_title.get((item.get("title") or item.get("name") or "").strip(), {}) or {}).get("id") or item.get("id"),
                    "title": item.get("title") or item.get("name"),
                    "repo": item.get("repo") or "curriculum",
                    "repo_path": f"curriculum/{month}.md",
                    "content_url": "https://openaitx.github.io/view.html?user=YOUR_GITHUB_USERNAME&project=curriculum&lang=zh-CN",
                    "share_url": item.get("share_url"),
                    "status": "published",
                }
                for item in share_results
            ],
            "commit": {"created": False, "pushed": False},
            "rebuild": {"triggered": False},
        }

    cmd = [
        sys.executable,
        str(PUBLISH_SCRIPT),
        "--month",
        month,
        "--batch-json",
        str(batch_json),
        "--items-json",
        str(items_json),
        "--skip-telegram",
        "--result-json",
        str(result_json),
        "--emit-json",
    ]
    if skip_push:
        cmd.append("--skip-push")
    if skip_rebuild:
        cmd.append("--skip-rebuild")
    cp = run_command(cmd, cwd=SCRIPT_DIR, timeout=None)
    if cp.returncode != 0:
        raise RuntimeError(f"mswnlz_publish.py 失败\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
    return read_json(result_json)


def attach_quark_results(items: List[Dict[str, Any]], batch_result: Dict[str, Any], result: Dict[str, Any]) -> None:
    share_results = batch_result.get("share_results") or []
    by_id = {str(entry.get("id")): entry for entry in share_results if entry.get("id") is not None}
    by_title = {str(entry.get("title") or entry.get("name")): entry for entry in share_results}

    for item in items:
        item_result = get_item_result(result, item["id"])
        match = by_id.get(str(item["id"])) or by_title.get(item["title"])
        if not match:
            item_result["quark"] = {"status": "failed"}
            append_error(item_result, "quark", "未在 batch_share_results 中找到对应资源")
            continue
        item_result["quark"] = {
            "status": match.get("status", "ok"),
            "source_url": match.get("source_url") or match.get("input_url") or item["url"],
            "share_url": match.get("share_url"),
            "raw": match,
        }


def attach_publish_results(items: List[Dict[str, Any]], publish_result: Dict[str, Any], result: Dict[str, Any]) -> None:
    published = publish_result.get("items") or []
    by_id = {str(entry.get("id")): entry for entry in published if entry.get("id") is not None}
    by_title = {str(entry.get("title")): entry for entry in published if entry.get("title")}

    for item in items:
        item_result = get_item_result(result, item["id"])
        match = by_id.get(str(item["id"])) or by_title.get(item["title"])
        if not match:
            item_result["publish"] = {"status": "skipped"}
            continue
        item_result["publish"] = {
            "status": match.get("status", "published"),
            "repo": match.get("repo"),
            "repo_path": match.get("repo_path"),
            "content_url": match.get("content_url"),
            "share_url": match.get("share_url"),
            "raw": match,
        }


def can_send_album(item: Dict[str, Any], item_result: Dict[str, Any], options: RunOptions) -> bool:
    if options.notify_mode != "album":
        return False
    if item.get("telegram", {}).get("mode", "album") != "album":
        return False
    return bool(item_result.get("images", {}).get("files"))


def handle_item(item: Dict[str, Any], item_result: Dict[str, Any], options: RunOptions, work_dir: Path, template_text: Optional[str]) -> None:
    image_cfg = item.get("image", {}) or {}
    if image_cfg.get("enabled", True):
        try:
            keywords = build_keywords_for_item(item)
            item_result["images"] = fetch_images_for_item(
                item=item,
                keywords=keywords,
                work_dir=work_dir,
                engine=options.image_engine or image_cfg.get("engine", "bing"),
                final_count=options.image_count or image_cfg.get("count", 3),
                candidate_count=options.image_candidates or image_cfg.get("candidates", 8),
                timeout_sec=options.image_timeout or image_cfg.get("timeout_sec", 90),
                min_width=image_cfg.get("min_width", 400),
                min_height=image_cfg.get("min_height", 400),
            )
        except Exception:
            item_result["images"] = {"status": "failed", "files": []}
            append_error(item_result, "images", traceback.format_exc())
            if options.fail_fast and not options.continue_on_image_error:
                raise
    else:
        item_result["images"] = {"status": "skipped", "files": [], "reason": "image.disabled=false"}

    caption = build_caption(item, item_result, template_text)
    item_result["telegram_caption"] = caption

    tg_cfg = item.get("telegram", {}) or {}
    if not tg_cfg.get("enabled", True) or options.notify_mode == "off":
        item_result["telegram"] = {"status": "skipped", "reason": "telegram.disabled"}
        return

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = options.telegram_chat_id or os.environ.get("TG_GROUP_1_ID", "")
    thread_id = options.telegram_thread_id or os.environ.get("TG_GROUP_1_THREAD", "") or None

    try:
        if can_send_album(item, item_result, options):
            tg_result = send_album_message(
                bot_token=bot_token,
                chat_id=chat_id,
                image_files=item_result["images"].get("files", []),
                caption=caption,
                message_thread_id=thread_id,
                parse_mode="HTML",
                disable_notification=tg_cfg.get("disable_notification", False),
            )
        else:
            tg_result = send_text_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=caption,
                message_thread_id=thread_id,
                parse_mode="HTML",
                disable_notification=tg_cfg.get("disable_notification", False),
            )
        item_result["telegram"] = tg_result.to_dict()
    except Exception as exc:
        append_error(item_result, "telegram", traceback.format_exc())
        if options.continue_on_telegram_error:
            try:
                fallback = send_text_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=caption,
                    message_thread_id=thread_id,
                    parse_mode="HTML",
                    disable_notification=tg_cfg.get("disable_notification", False),
                )
                item_result["telegram"] = {
                    "status": "partial",
                    "mode": "text_fallback",
                    "error": str(exc),
                    "fallback": fallback.to_dict(),
                }
            except Exception as fallback_exc:
                item_result["telegram"] = {
                    "status": "failed",
                    "mode": "album_or_text",
                    "error": str(exc),
                    "fallback_error": str(fallback_exc),
                }
                if options.fail_fast:
                    raise
        else:
            item_result["telegram"] = {"status": "failed", "error": str(exc)}
            if options.fail_fast:
                raise


def finalize_result(result: Dict[str, Any]) -> None:
    result["finished_at"] = now_iso()
    published_ok = 0
    image_ok = 0
    telegram_ok = 0
    partial = 0
    failed = 0

    for item_result in result["items"]:
        if item_result.get("publish", {}).get("status") in {"published", "ok"}:
            published_ok += 1
        if item_result.get("images", {}).get("status") == "ok":
            image_ok += 1
        if item_result.get("telegram", {}).get("status") == "ok":
            telegram_ok += 1

        statuses = [
            item_result.get("quark", {}).get("status"),
            item_result.get("publish", {}).get("status"),
            item_result.get("images", {}).get("status"),
            item_result.get("telegram", {}).get("status"),
        ]
        if "failed" in statuses:
            failed += 1
        elif "partial" in statuses:
            partial += 1

    result["summary"] = {
        "total": len(result["items"]),
        "published_ok": published_ok,
        "image_ok": image_ok,
        "telegram_ok": telegram_ok,
        "partial": partial,
        "failed": failed,
    }
    if failed == 0 and partial == 0:
        result["status"] = "ok"
    elif failed == 0:
        result["status"] = "partial_success"
    else:
        result["status"] = "failed"


def parse_args() -> RunOptions:
    ap = argparse.ArgumentParser(description="夸克资源发布 + Bing 抓图 + Telegram 图文推送")
    ap.add_argument("--month", required=True)
    ap.add_argument("--items-json", required=True)
    ap.add_argument("--result-json", required=True)
    ap.add_argument("--work-dir", default="./tmp_publish_with_images")
    ap.add_argument("--label")
    ap.add_argument("--skip-quark", action="store_true")
    ap.add_argument("--batch-json")
    ap.add_argument("--skip-publish", action="store_true")
    ap.add_argument("--skip-push", action="store_true")
    ap.add_argument("--skip-rebuild", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--image-engine", default="bing")
    ap.add_argument("--image-count", type=int)
    ap.add_argument("--image-candidates", type=int)
    ap.add_argument("--image-timeout", type=int)
    ap.add_argument("--telegram-chat-id")
    ap.add_argument("--telegram-thread-id")
    ap.add_argument("--notify-mode", default="album", choices=["album", "text", "off"])
    ap.add_argument("--caption-template")
    ap.add_argument("--send-summary", action="store_true")
    ap.add_argument("--continue-on-image-error", action="store_true", default=True)
    ap.add_argument("--continue-on-telegram-error", action="store_true", default=True)
    ap.add_argument("--fail-fast", action="store_true")
    ns = ap.parse_args()
    return RunOptions(
        month=ns.month,
        items_json=Path(ns.items_json),
        result_json=Path(ns.result_json),
        work_dir=Path(ns.work_dir),
        label=ns.label,
        skip_quark=ns.skip_quark,
        batch_json=Path(ns.batch_json) if ns.batch_json else None,
        skip_publish=ns.skip_publish,
        skip_push=ns.skip_push,
        skip_rebuild=ns.skip_rebuild,
        dry_run=ns.dry_run,
        image_engine=ns.image_engine,
        image_count=ns.image_count,
        image_candidates=ns.image_candidates,
        image_timeout=ns.image_timeout,
        telegram_chat_id=ns.telegram_chat_id,
        telegram_thread_id=ns.telegram_thread_id,
        notify_mode=ns.notify_mode,
        caption_template=Path(ns.caption_template) if ns.caption_template else None,
        send_summary=ns.send_summary,
        continue_on_image_error=ns.continue_on_image_error,
        continue_on_telegram_error=ns.continue_on_telegram_error,
        fail_fast=ns.fail_fast,
    )


def main() -> int:
    options = parse_args()
    run_id = make_run_id()
    work_dir = options.work_dir / run_id
    work_dir.mkdir(parents=True, exist_ok=True)

    cfg = read_json(options.items_json)
    validate_config(cfg)
    items = normalize_items(cfg)
    result = init_result(run_id, options, items)
    template_text = load_caption_template(options.caption_template)

    try:
        if options.skip_quark:
            if not options.batch_json:
                raise ValueError("--skip-quark 时必须传 --batch-json")
            batch_result = read_json(options.batch_json)
            batch_json_path = options.batch_json
        else:
            batch_json_path = work_dir / "batch_share_results.json"
            batch_result = run_quark_batch(items, options.month, options.label, batch_json_path, dry_run=options.dry_run)
            write_json(batch_json_path, batch_result)
        attach_quark_results(items, batch_result, result)

        if options.skip_publish:
            publish_result = {"items": []}
        else:
            publish_result_json = work_dir / "publish_result.json"
            publish_result = run_publish(
                options.month,
                batch_json_path,
                publish_result_json,
                options.items_json,
                skip_push=options.skip_push,
                skip_rebuild=options.skip_rebuild,
                dry_run=options.dry_run,
            )
            write_json(publish_result_json, publish_result)
        attach_publish_results(items, publish_result, result)

        for item in items:
            item_result = get_item_result(result, item["id"])
            handle_item(item, item_result, options, work_dir, template_text)

        finalize_result(result)
        write_json(options.result_json, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        log(f"FATAL: {exc}")
        traceback.print_exc()
        result["status"] = "failed"
        result["fatal_error"] = str(exc)
        result["finished_at"] = now_iso()
        write_json(options.result_json, result)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
