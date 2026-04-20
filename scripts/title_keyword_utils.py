#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from typing import Dict, List

NOISE_PATTERNS = [
    r"\b4K\b",
    r"\b8K\b",
    r"\b1080P\b",
    r"\b720P\b",
    r"\b2160P\b",
    r"\bHDR\b",
    r"\bHEVC\b",
    r"\bBluRay\b",
    r"\bWEB[- ]?DL\b",
    r"\bWEBRip\b",
    r"\b国语\b",
    r"\b中字\b",
    r"\b中英双字\b",
    r"\b双语\b",
    r"\b全集\b",
    r"\b完整版\b",
    r"\b收藏版\b",
    r"\b无删减\b",
    r"\b更新至\d+集\b",
    r"\b全\d+集\b",
    r"\[[^\]]+\]",
    r"\([^)]+\)",
    r"（[^）]+）",
]


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def strip_noise(title: str) -> str:
    cleaned = title or ""
    for pattern in NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[_\-—|]+", " ", cleaned)
    cleaned = re.sub(r"\.+", " ", cleaned)
    return normalize_spaces(cleaned)


def dedupe_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in items:
        text = normalize_spaces(str(value))
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def guess_resource_type(item: Dict) -> str:
    category = str(item.get("category", "")).strip().lower()
    if category in {"movie", "tv", "anime", "drama", "video", "movies"}:
        return "video"
    if category in {"book", "ebook", "books"}:
        return "book"
    if category in {"course", "tutorial", "curriculum"}:
        return "course"
    if category in {"software", "app", "tool", "tools"}:
        return "software"

    title = str(item.get("title", ""))
    if re.search(r"电影|剧|纪录片|演唱会|动画|番剧", title):
        return "video"
    if re.search(r"书|杂志|电子书|小说", title):
        return "book"
    if re.search(r"课程|教程|训练营|实战|刷题", title):
        return "course"
    if re.search(r"软件|工具|插件|客户端|APP|apk|ipa", title, re.IGNORECASE):
        return "software"
    return "generic"


def build_visual_suffixes(resource_type: str) -> List[str]:
    if resource_type == "video":
        return ["海报", "剧照", "poster"]
    if resource_type == "book":
        return ["封面", "书籍封面", "book cover"]
    if resource_type == "course":
        return ["课程封面", "培训海报", "cover"]
    if resource_type == "software":
        return ["logo", "screenshot", "界面截图"]
    return ["海报", "封面", "poster"]


def extract_main_title(title: str) -> str:
    cleaned = strip_noise(title)
    return cleaned or normalize_spaces(title)


def build_auto_keywords(item: Dict) -> List[str]:
    raw_title = normalize_spaces(str(item.get("title", "")))
    main_title = extract_main_title(raw_title)
    resource_type = guess_resource_type(item)
    suffixes = build_visual_suffixes(resource_type)

    keywords: List[str] = []
    for suffix in suffixes:
        keywords.append(f"{main_title} {suffix}")

    if raw_title and raw_title != main_title:
        keywords.append(raw_title)
    if main_title and re.search(r"[A-Za-z]", main_title):
        keywords.append(main_title)

    return dedupe_keep_order(keywords)


def build_keywords_for_item(item: Dict) -> List[str]:
    image_cfg = item.get("image", {}) or {}
    manual = image_cfg.get("keywords") or []
    if manual:
        return dedupe_keep_order([str(v) for v in manual])
    return build_auto_keywords(item)
