"""Shared helpers for the quark-mswnlz-publisher skill.

This skill was upstreamed with author-specific absolute paths. These helpers replace
those assumptions with environment-variable based discovery so the skill can be
moved across machines without patching every script again.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

SKILL_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = SKILL_ROOT.parent.parent


def _unique_paths(paths: Iterable[Optional[Path]]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        if not path:
            continue
        p = Path(path).expanduser()
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _first_existing_dir(paths: Iterable[Optional[Path]]) -> Optional[Path]:
    for path in _unique_paths(paths):
        if path.is_dir():
            return path.resolve()
    return None


def _first_existing_file(paths: Iterable[Optional[Path]]) -> Optional[Path]:
    for path in _unique_paths(paths):
        if path.is_file():
            return path.resolve()
    return None


def get_project_root() -> Optional[Path]:
    value = os.environ.get("QUARK_MSWNLZ_PROJECT_ROOT")
    if not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.exists() else path


def get_quark_root(*, explicit: str = "", require: bool = False) -> Optional[Path]:
    project_root = get_project_root()
    path = _first_existing_dir(
        [
            Path(explicit).expanduser() if explicit else None,
            Path(os.environ["QUARK_PAN_TOOL_ROOT"]).expanduser() if os.environ.get("QUARK_PAN_TOOL_ROOT") else None,
            Path(os.environ["QUARK_TOOL_ROOT"]).expanduser() if os.environ.get("QUARK_TOOL_ROOT") else None,
            project_root / "QuarkPanTool" if project_root else None,
            WORKSPACE_ROOT / "QuarkPanTool",
        ]
    )
    if path or not require:
        return path
    raise FileNotFoundError(
        "找不到 QuarkPanTool 目录。请设置 QUARK_PAN_TOOL_ROOT（或 QUARK_TOOL_ROOT / QUARK_MSWNLZ_PROJECT_ROOT），"
        f"当前默认检查过的工作区路径是：{WORKSPACE_ROOT / 'QuarkPanTool'}"
    )


def get_mswnlz_root(*, explicit: str = "", require: bool = False) -> Optional[Path]:
    project_root = get_project_root()
    path = _first_existing_dir(
        [
            Path(explicit).expanduser() if explicit else None,
            Path(os.environ["MSWNLZ_ROOT"]).expanduser() if os.environ.get("MSWNLZ_ROOT") else None,
            project_root / "mswnlz" if project_root else None,
            WORKSPACE_ROOT / "mswnlz",
        ]
    )
    if path or not require:
        return path
    raise FileNotFoundError(
        "找不到 mswnlz 仓库目录。请设置 MSWNLZ_ROOT（或 QUARK_MSWNLZ_PROJECT_ROOT）。"
    )


def get_site_repo_dir(*, explicit: str = "", require: bool = False) -> Optional[Path]:
    mswnlz_root = get_mswnlz_root(require=False)
    path = _first_existing_dir(
        [
            Path(explicit).expanduser() if explicit else None,
            Path(os.environ["MSWNLZ_SITE_REPO_DIR"]).expanduser() if os.environ.get("MSWNLZ_SITE_REPO_DIR") else None,
            mswnlz_root / "xi7ang.github.io" if mswnlz_root else None,
            mswnlz_root / "YOUR_USERNAME.github.io" if mswnlz_root else None,
            WORKSPACE_ROOT / "xi7ang.github.io",
            WORKSPACE_ROOT / "YOUR_USERNAME.github.io",
        ]
    )
    if path or not require:
        return path
    raise FileNotFoundError(
        "找不到站点仓库目录。请设置 MSWNLZ_SITE_REPO_DIR，或确保 MSWNLZ_ROOT/<username>.github.io 存在。"
    )


def get_default_cookies_file(*, explicit: str = "") -> Optional[Path]:
    quark_root = get_quark_root(require=False)
    return _first_existing_file(
        [
            Path(explicit).expanduser() if explicit else None,
            Path(os.environ["QUARK_COOKIES_FILE"]).expanduser() if os.environ.get("QUARK_COOKIES_FILE") else None,
            quark_root / "config" / "cookies.txt" if quark_root else None,
        ]
    )


def load_env_files() -> list[Path]:
    quark_root = get_quark_root(require=False)
    loaded: list[Path] = []
    candidates = _unique_paths(
        [
            Path(os.environ["QUARK_MSWNLZ_ENV_FILE"]).expanduser() if os.environ.get("QUARK_MSWNLZ_ENV_FILE") else None,
            quark_root / "config" / "secrets.env" if quark_root else None,
            SKILL_ROOT / ".env",
        ]
    )

    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
        loaded.append(path.resolve())
    return loaded


def prepend_sys_path(path: Path) -> None:
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


# ── Checkpoint / Run State ───────────────────────────────────────────────


from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent


def quark_url_to_key(quark_url: str) -> str:
    return hashlib.md5(quark_url.encode()).hexdigest()[:12]


def work_dir_for_url(quark_url: str) -> Path:
    key = quark_url_to_key(quark_url)
    return SCRIPT_DIR / "tmp_forward" / key


def checkpoint_path(work_dir: Path) -> Path:
    return work_dir / "run_state.json"


def load_checkpoint(work_dir: Path) -> Optional[Dict[str, Any]]:
    path = checkpoint_path(work_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == "running":
            return data
        return None
    except Exception:
        return None


def save_checkpoint(
    work_dir: Path,
    completed_steps: list[str],
    step_outputs: Dict[str, Any],
    parsed: Optional[Dict[str, Any]] = None,
    status: str = "running",
) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(work_dir)
    tmp_path = path.with_suffix(".tmp")
    now = dt.datetime.now().astimezone().isoformat()
    url_key = quark_url_to_key(parsed.get("quark_url", "") if parsed else "")
    data: Dict[str, Any] = {
        "run_id": url_key,
        "status": status,
        "completed_steps": completed_steps,
        "step_outputs": step_outputs,
        "started_at": now,
        "finished_at": None,
    }
    if parsed is not None:
        data["parsed"] = parsed
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def update_checkpoint_step(
    work_dir: Path,
    step_name: str,
    step_output: Any,
    parsed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = load_checkpoint(work_dir)
    if data is None:
        url_key = quark_url_to_key(parsed.get("quark_url", "") if parsed else "")
        data = {"run_id": url_key, "status": "running", "completed_steps": [], "step_outputs": {}}
    steps = list(data.get("completed_steps", []))
    if step_name not in steps:
        steps.append(step_name)
    outputs = dict(data.get("step_outputs", {}))
    outputs[step_name] = step_output
    save_checkpoint(
        work_dir,
        completed_steps=steps,
        step_outputs=outputs,
        parsed=parsed or data.get("parsed"),
    )
    data["completed_steps"] = steps
    data["step_outputs"] = outputs
    return data


def is_step_done(cp_data: Dict[str, Any], step_name: str) -> bool:
    return step_name in cp_data.get("completed_steps", [])


def get_step_output(cp_data: Dict[str, Any], step_name: str) -> Any:
    return cp_data.get("step_outputs", {}).get(step_name)
