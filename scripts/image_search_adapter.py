#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _list_images(path: Path) -> List[Path]:
    if not path.exists():
        return []
    return sorted([p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS])


def _filter_images_by_size(files: List[Path], min_width: int, min_height: int) -> List[Path]:
    valid: List[Path] = []
    if Image is None:
        return list(files)
    for file in files:
        try:
            with Image.open(file) as img:
                width, height = img.size
            if width >= min_width and height >= min_height:
                valid.append(file)
        except Exception:
            continue
    return valid


def _pick_top_n(files: List[Path], final_count: int) -> List[Path]:
    return files[:final_count]


def _build_command(crawler_script: Path, keywords: List[str], output_dir: Path, engine: str, candidate_count: int) -> List[str]:
    cmd = [
        sys.executable,
        str(crawler_script),
        "-n",
        str(candidate_count),
        "-o",
        str(output_dir),
        "-e",
        engine,
        "--json",
    ]
    for keyword in keywords:
        cmd.extend(["-k", keyword])
    return cmd


def _run_crawler(cmd: List[str], timeout_sec: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_sec,
        check=False,
    )


def _parse_crawler_output(cp: subprocess.CompletedProcess) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    done: Optional[Dict[str, Any]] = None
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            events.append(obj)
            if obj.get("type") == "done":
                done = obj
    return {
        "returncode": cp.returncode,
        "stdout": cp.stdout,
        "stderr": cp.stderr,
        "done": done,
        "events": events,
    }


def fetch_images_for_item(
    item: Dict[str, Any],
    keywords: List[str],
    work_dir: Path,
    engine: str = "bing",
    final_count: int = 3,
    candidate_count: int = 8,
    timeout_sec: int = 90,
    min_width: int = 400,
    min_height: int = 400,
    crawler_script: Optional[Path] = None,
) -> Dict[str, Any]:
    item_id = str(item["id"])
    output_dir = work_dir / "images" / item_id
    _ensure_dir(output_dir)

    if crawler_script is None:
        crawler_script = Path("/root/.openclaw/workspace/skills/image-crawler/scripts/image_crawler.py")
    if not crawler_script.exists():
        raise FileNotFoundError(f"找不到 image_crawler.py: {crawler_script}")
    if not keywords:
        raise ValueError(f"{item_id} 没有可用关键词")

    cmd = _build_command(crawler_script, keywords, output_dir, engine, candidate_count)
    cp = _run_crawler(cmd, timeout_sec=timeout_sec)
    crawler_meta = _parse_crawler_output(cp)

    all_files = _list_images(output_dir)
    valid_files = _filter_images_by_size(all_files, min_width=min_width, min_height=min_height)
    selected_files = _pick_top_n(valid_files, final_count=final_count)

    if selected_files:
        status = "ok" if len(selected_files) >= final_count else "partial"
    else:
        status = "failed"

    return {
        "status": status,
        "engine": engine,
        "keywords_used": keywords,
        "requested": final_count,
        "candidates_requested": candidate_count,
        "downloaded": len(all_files),
        "valid": len(valid_files),
        "selected": len(selected_files),
        "files": [str(path) for path in selected_files],
        "output_dir": str(output_dir),
        "crawler": crawler_meta,
    }
