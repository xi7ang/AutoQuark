#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass
class TelegramNotifyResult:
    status: str
    mode: str
    ok: bool
    message_id: Optional[int] = None
    raw: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "ok": self.ok,
            "message_id": self.message_id,
            "raw": self.raw,
            "error": self.error,
        }


def _api_url(bot_token: str, method: str) -> str:
    token = (bot_token or "").strip()
    if not token:
        raise ValueError("缺少 Telegram bot token")
    return f"{TELEGRAM_API_BASE}/bot{token}/{method}"


def send_text_message(
    bot_token: str,
    chat_id: str,
    text: str,
    message_thread_id: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    disable_notification: bool = False,
    timeout: int = 60,
) -> TelegramNotifyResult:
    if not str(chat_id).strip():
        raise ValueError("缺少 Telegram chat_id")
    if not (text or "").strip():
        raise ValueError("text 为空")

    payload: Dict[str, Any] = {
        "chat_id": str(chat_id),
        "text": text,
        "disable_notification": disable_notification,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if message_thread_id:
        payload["message_thread_id"] = str(message_thread_id)

    resp = requests.post(_api_url(bot_token, "sendMessage"), json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage 失败: {data}")
    message = data.get("result", {})
    return TelegramNotifyResult(status="ok", mode="text", ok=True, message_id=message.get("message_id"), raw=data)


def send_album_message(
    bot_token: str,
    chat_id: str,
    image_files: List[str],
    caption: str,
    message_thread_id: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    disable_notification: bool = False,
    timeout: int = 120,
) -> TelegramNotifyResult:
    if not str(chat_id).strip():
        raise ValueError("缺少 Telegram chat_id")
    if not image_files:
        raise ValueError("image_files 为空")
    if len(image_files) > 10:
        raise ValueError("Telegram 媒体组最多 10 张")

    media: List[Dict[str, Any]] = []
    files: Dict[str, Any] = {}
    try:
        for idx, path_str in enumerate(image_files):
            path = Path(path_str)
            if not path.exists():
                raise FileNotFoundError(f"图片不存在: {path}")
            attach_name = f"file{idx}"
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            files[attach_name] = (path.name, path.open("rb"), mime)
            item: Dict[str, Any] = {"type": "photo", "media": f"attach://{attach_name}"}
            if idx == 0 and caption:
                item["caption"] = caption
                if parse_mode:
                    item["parse_mode"] = parse_mode
            media.append(item)

        data: Dict[str, Any] = {
            "chat_id": str(chat_id),
            "media": json.dumps(media, ensure_ascii=False),
            "disable_notification": str(disable_notification).lower(),
        }
        if message_thread_id:
            data["message_thread_id"] = str(message_thread_id)

        resp = requests.post(_api_url(bot_token, "sendMediaGroup"), data=data, files=files, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram sendMediaGroup 失败: {payload}")
        results = payload.get("result", [])
        first_message_id = results[0].get("message_id") if results else None
        return TelegramNotifyResult(status="ok", mode="album", ok=True, message_id=first_message_id, raw=payload)
    finally:
        for file_tuple in files.values():
            try:
                file_tuple[1].close()
            except Exception:
                pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="发送 Telegram 图文相册或纯文本消息")
    p.add_argument("--bot-token", required=True)
    p.add_argument("--chat-id", required=True)
    p.add_argument("--thread-id")
    p.add_argument("--caption-text")
    p.add_argument("--caption-file")
    p.add_argument("--image", action="append", default=[])
    p.add_argument("--parse-mode", default="HTML")
    p.add_argument("--disable-notification", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    caption = args.caption_text or ""
    if args.caption_file:
        caption = Path(args.caption_file).read_text(encoding="utf-8")

    if args.image:
        result = send_album_message(
            bot_token=args.bot_token,
            chat_id=args.chat_id,
            image_files=args.image,
            caption=caption,
            message_thread_id=args.thread_id,
            parse_mode=args.parse_mode,
            disable_notification=args.disable_notification,
        )
    else:
        result = send_text_message(
            bot_token=args.bot_token,
            chat_id=args.chat_id,
            text=caption,
            message_thread_id=args.thread_id,
            parse_mode=args.parse_mode,
            disable_notification=args.disable_notification,
        )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.to_dict())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
