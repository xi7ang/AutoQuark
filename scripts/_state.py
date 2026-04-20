"""Batch run state manager — idempotency & checkpoint for quark-mswnlz-publisher.

State directory: /root/.openclaw/workspace/batch_run_states/

State files:
  <batch_id>_status.json   — per-batch state (transferred, shared, repos_updated, tg_notified)
  link_registry.json       — cross-batch URL dedup registry

A "batch_id" is the batch_folder_name (e.g. "2026-04-19_0930_短裤哥批次").
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import get_mswnlz_root

# ── Directory ────────────────────────────────────────────────────────────

BATCH_RUN_STATES_DIR = Path("/root/.openclaw/workspace/batch_run_states")
LINK_REGISTRY_FILE = BATCH_RUN_STATES_DIR / "link_registry.json"


def _ensure_states_dir() -> Path:
    BATCH_RUN_STATES_DIR.mkdir(parents=True, exist_ok=True)
    return BATCH_RUN_STATES_DIR


def _batch_status_path(batch_id: str) -> Path:
    return _ensure_states_dir() / f"{_safe_filename(batch_id)}_status.json"


def _safe_filename(name: str) -> str:
    """Make a batch_id safe for use as a filename."""
    return name.replace("/", "_").replace("\\", "_").replace(":", "_").strip()


# ── Link Registry (cross-batch dedup) ────────────────────────────────────

def _load_link_registry() -> Dict[str, Dict[str, Any]]:
    """Returns {url_key: {title, share_url, batch_id, transferred_at}}."""
    if LINK_REGISTRY_FILE.exists():
        try:
            return json.loads(LINK_REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _url_key(quark_url: str) -> str:
    return hashlib.md5(quark_url.encode()).hexdigest()[:16]


def is_url_processed(quark_url: str) -> bool:
    """Check if this URL has already been successfully transferred in any batch."""
    return _url_key(quark_url) in _load_link_registry()


def register_url(quark_url: str, title: str, share_url: str, batch_id: str) -> None:
    """Register a URL as processed (after transfer + share succeed)."""
    key = _url_key(quark_url)
    registry = _load_link_registry()
    import datetime
    registry[key] = {
        "title": title,
        "share_url": share_url,
        "batch_id": batch_id,
        "transferred_at": datetime.datetime.now().astimezone().isoformat(),
    }
    LINK_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    LINK_REGISTRY_FILE.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_share_from_registry(quark_url: str) -> Optional[Dict[str, str]]:
    """Return the cached share result for a URL, or None."""
    return _load_link_registry().get(_url_key(quark_url))


# ── Batch State ───────────────────────────────────────────────────────────

def load_batch_state(batch_id: str) -> Dict[str, Any]:
    """Load per-batch state. Returns empty dict for new batches."""
    path = _batch_status_path(batch_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return new_batch_state(batch_id)


def new_batch_state(batch_id: str) -> Dict[str, Any]:
    return {
        "batch_id": batch_id,
        "transferred": {},    # {title: {"fid", "input_url", "status": "ok"}}
        "shared": {},         # {title: {"share_url", "share_id", "fid"}}
        "repos_updated": [],  # [repo_name, ...]
        "tg_notified": {},    # {chat_id: {"at": iso_time, "chunks": n}}
    }


def save_batch_state(batch_id: str, state: Dict[str, Any]) -> None:
    """Persist batch state to disk atomically."""
    path = _batch_status_path(batch_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ── Convenience helpers (used by quark_batch_run.py) ─────────────────────

def mark_transferred(batch_id: str, title: str, fid: str, input_url: str) -> Dict[str, Any]:
    """Record that a single item was transferred (not yet shared)."""
    state = load_batch_state(batch_id)
    state["transferred"][title] = {"fid": fid, "input_url": input_url, "status": "ok"}
    save_batch_state(batch_id, state)
    return state


def is_transferred(batch_id: str, title: str) -> bool:
    state = load_batch_state(batch_id)
    return title in state.get("transferred", {})


def get_transferred_fid(batch_id: str, title: str) -> Optional[str]:
    state = load_batch_state(batch_id)
    return state.get("transferred", {}).get(title, {}).get("fid")


def mark_shared(batch_id: str, title: str, share_url: str, share_id: str, fid: str) -> None:
    """Record that a share link was generated for an item."""
    state = load_batch_state(batch_id)
    state["shared"][title] = {"share_url": share_url, "share_id": share_id, "fid": fid}
    save_batch_state(batch_id, state)


def is_shared(batch_id: str, title: str) -> bool:
    state = load_batch_state(batch_id)
    return title in state.get("shared", {})


def get_share_result(batch_id: str, title: str) -> Optional[Dict[str, str]]:
    return load_batch_state(batch_id).get("shared", {}).get(title)


def mark_repo_updated(batch_id: str, repo: str) -> None:
    state = load_batch_state(batch_id)
    if repo not in state.get("repos_updated", []):
        state.setdefault("repos_updated", []).append(repo)
        save_batch_state(batch_id, state)


def is_repo_updated(batch_id: str, repo: str) -> bool:
    return repo in load_batch_state(batch_id).get("repos_updated", [])


# ── Convenience helpers (used by mswnlz_publish.py) ──────────────────────

def mark_tg_notified(batch_id: str, chat_id: str, chunks: int) -> None:
    """Record that Telegram notification was sent to a specific group."""
    state = load_batch_state(batch_id)
    import datetime
    state.setdefault("tg_notified", {})[chat_id] = {
        "at": datetime.datetime.now().astimezone().isoformat(),
        "chunks": chunks,
    }
    save_batch_state(batch_id, state)


def get_tg_notified_groups(batch_id: str) -> List[str]:
    """Return list of chat_ids already notified for this batch."""
    return list(load_batch_state(batch_id).get("tg_notified", {}).keys())


def is_tg_notified(batch_id: str, chat_id: str) -> bool:
    return chat_id in get_tg_notified_groups(batch_id)


# ── Combined checkpoint for quark_batch_run (full item recovery) ─────────

def load_quark_run_state(batch_id: str) -> Dict[str, Any]:
    """Load full run state including recoverable share_results list."""
    return load_batch_state(batch_id)


def recover_share_results(batch_id: str) -> List[Dict[str, Any]]:
    """Return share_results already computed for this batch (for resume)."""
    state = load_batch_state(batch_id)
    shared = state.get("shared", {})
    transferred = state.get("transferred", {})
    results = []
    for title, share_info in shared.items():
        results.append({
            "id": "",
            "title": title,
            "name": title,
            "fid": share_info.get("fid", ""),
            "share_id": share_info.get("share_id", ""),
            "share_url": share_info.get("share_url", ""),
            "status": "ok",
        })
    return results


def is_item_complete_for_batch(batch_id: str, title: str) -> bool:
    """True if this item has both transferred and shared results."""
    state = load_batch_state(batch_id)
    return (
        title in state.get("transferred", {})
        and title in state.get("shared", {})
    )
