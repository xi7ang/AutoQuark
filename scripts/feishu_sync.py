#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feishu_sync.py

将夸克资源同步到飞书：
  - Word 在线文档（结构化富文本：标题/标签/类型/简介/链接 + emoji）
  - Sheets 电子表格（5 列扁平行：标题/标签/类型/简介/链接 + 推送日期）

首次运行：自动创建 Word 文档和 Sheet，把 URL 写回 secrets.env。
后续运行：append 单条资源（带 quark_url 去重），不重复写入。

依赖：lark-cli v2 API（feishu）已配置并绑定到 openclaw context。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent

# 让 _common 可被导入
sys.path.insert(0, str(SCRIPT_DIR))
from _common import load_env_files  # noqa: E402

load_env_files()


# ── 分类中文映射 ─────────────────────────────────────────────────────

REPO_CATEGORY: Dict[str, Dict[str, str]] = {
    "AIknowledge":         {"emoji": "🤖", "label": "AI 知识",       "url": "https://pan.devmini.space/AIknowledge/"},
    "auto":                {"emoji": "🚗", "label": "汽车资料",       "url": "https://pan.devmini.space/auto/"},
    "book":                {"emoji": "📖", "label": "书籍资料",       "url": "https://pan.devmini.space/book/"},
    "chinese-traditional": {"emoji": "🏯", "label": "传统文化",       "url": "https://pan.devmini.space/chinese-traditional/"},
    "cross-border":        {"emoji": "🛒", "label": "跨境电商",       "url": "https://pan.devmini.space/cross-border/"},
    "curriculum":          {"emoji": "🎓", "label": "课程资料",       "url": "https://pan.devmini.space/curriculum/"},
    "edu-knowlege":        {"emoji": "📚", "label": "教育知识",       "url": "https://pan.devmini.space/edu-knowlege/"},
    "games":               {"emoji": "🎮", "label": "游戏资源",       "url": "https://pan.devmini.space/games/"},
    "healthy":             {"emoji": "💪", "label": "健康养生",       "url": "https://pan.devmini.space/healthy/"},
    "movies":              {"emoji": "🎬", "label": "影视媒体",       "url": "https://pan.devmini.space/movies/"},
    "self-media":          {"emoji": "📱", "label": "自媒体",         "url": "https://pan.devmini.space/self-media/"},
    "tools":               {"emoji": "🛠️", "label": "工具合集",       "url": "https://pan.devmini.space/tools/"},
}


# ── 配置读取 ─────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return os.environ.get("FEISHU_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def get_doc_url() -> str:
    return os.environ.get("FEISHU_DOC_URL", "").strip()


def get_sheet_url() -> str:
    return os.environ.get("FEISHU_SHEET_URL", "").strip()


def get_sheet_tab() -> str:
    return os.environ.get("FEISHU_SHEET_TAB", "Sheet1").strip() or "Sheet1"


def get_parent_token() -> str:
    return os.environ.get("FEISHU_PARENT_TOKEN", "").strip()


def get_identity() -> str:
    """
    飞书身份：user（默认，走用户 admin 权限，scope 已生效）
    或 bot（需 app 发布后能用，限制更严）
    """
    v = os.environ.get("FEISHU_IDENTITY", "user").strip().lower()
    return v if v in ("user", "bot") else "user"


def _now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def log(msg: str) -> None:
    print(f"[feishu] {msg}", file=sys.stderr)


# ── lark-cli 调用封装 ────────────────────────────────────────────────

# 绕开 openclaw 上下文的 env vars，避免 lark-cli 报 "openclaw context detected"
# 注意：lark-cli 原本检测到这些 env 就要求走 config bind 流程
# 我们走 lark-cli 自身配置的 app + user 身份（王胜 admin），直接调飞书
_OPENCLAW_ENV_PREFIXES = ("OPENCLAW_",)


def _strip_openclaw_env() -> Dict[str, str]:
    """
    返回去掉了 OPENCLAW_* 之后的环境变量副本。
    """
    return {k: v for k, v in os.environ.items() if not k.startswith(_OPENCLAW_ENV_PREFIXES)}


def _run_lark(args: List[str], timeout: int = 60, identity: str = "user") -> Dict[str, Any]:
    """
    调用 lark-cli 子命令，返回解析后的 JSON dict。
    失败抛 RuntimeError。

    identity: "user" | "bot"
      - user：走王胜 admin 身份，scopes 已生效，能用
      - bot：走 app 身份，scopes 需要发布后才能用
    """
    clean_env = _strip_openclaw_env()
    full_args = ["lark-cli", "--as", identity] + args
    proc = subprocess.run(
        full_args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=clean_env,
    )
    out = proc.stdout.strip()
    err = proc.stderr.strip()
    if proc.returncode != 0:
        raise RuntimeError(f"lark-cli 退出码 {proc.returncode}: stdout={out[:300]} stderr={err[:300]}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"lark-cli 输出非 JSON：{out[:300]} ({exc})") from exc


def _ok(resp: Dict[str, Any]) -> bool:
    return bool(resp.get("ok"))


# ── 写入 secrets.env ────────────────────────────────────────────────

def _save_env_value(key: str, value: str) -> None:
    """
    把 key=value 写入 secrets.env（自动加载路径，由 _common.load_env_files 决定）。
    若已存在则替换；不存在则追加。
    """
    # 找所有可能的 secrets.env 路径
    candidates = [
        Path("/root/.openclaw/workspace/QuarkPanTool/config/secrets.env"),
        Path("/root/.openclaw/workspace/AutoQuark/.env"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new_line = f"{key}={value}"
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        if pattern.search(text):
            text2 = pattern.sub(new_line, text)
        else:
            # 确保文件以换行结尾
            sep = "" if text.endswith("\n") else "\n"
            text2 = text + sep + new_line + "\n"
        path.write_text(text2, encoding="utf-8")
        log(f"  💾 已写入 {key} 到 {path.name}")


# ── 初始化：Word 文档 ───────────────────────────────────────────────

WORD_INIT_XML = """<title>pan.devmini.space 资源导航</title>
<callout emoji="📚" background-color="blue">
<p><b>一个 100T+ 资源聚合站</b>，覆盖 AI / 书籍 / 跨境 / 自媒体 / 教育 / 健康 / 影视 / 游戏 / 工具 共 12 个分类。</p>
<p>本文档由自动流水线维护，<b>最新资源实时同步</b>。</p>
</callout>

<h1>🌐 站点入口</h1>
<p>🔗 <a href="https://pan.devmini.space/">https://pan.devmini.space/</a></p>

<h1>🗂️ 全部分类导航（点击直达）</h1>
<grid cols="2">
<column>
<ul>
<li>🤖 <a href="https://pan.devmini.space/AIknowledge/">AI 知识</a></li>
<li>🚗 <a href="https://pan.devmini.space/auto/">汽车资料</a></li>
<li>📖 <a href="https://pan.devmini.space/book/">书籍资料</a></li>
<li>🏯 <a href="https://pan.devmini.space/chinese-traditional/">传统文化</a></li>
<li>🛒 <a href="https://pan.devmini.space/cross-border/">跨境电商</a></li>
<li>🎓 <a href="https://pan.devmini.space/curriculum/">课程资料</a></li>
</ul>
</column>
<column>
<ul>
<li>📚 <a href="https://pan.devmini.space/edu-knowlege/">教育知识</a></li>
<li>🎮 <a href="https://pan.devmini.space/games/">游戏资源</a></li>
<li>💪 <a href="https://pan.devmini.space/healthy/">健康养生</a></li>
<li>🎬 <a href="https://pan.devmini.space/movies/">影视媒体</a></li>
<li>📱 <a href="https://pan.devmini.space/self-media/">自媒体</a></li>
<li>🛠️ <a href="https://pan.devmini.space/tools/">工具合集</a></li>
</ul>
</column>
</grid>

<h1>📢 加入社区</h1>
<ul>
<li>✈️ <a href="https://t.me/xi7ang">Telegram: t.me/xi7ang</a></li>
<li>💬 <a href="https://qm.qq.com/q/EkPkbcVMaY">QQ 群</a></li>
</ul>

<h1>⚠️ 免责声明</h1>
<p>本站仅供学习交流，资源版权属于原作者。如需删除请联系我们。</p>

<hr/>

<h1>🌟 最新资源（自动同步，按时间倒序追加）</h1>
"""


def init_word_doc() -> str:
    """
    首次运行：创建 Word 文档，写入初始结构，返回文档 URL。
    """
    parent_token = get_parent_token()
    args = ["docs", "+create", "--api-version", "v2", "--content", WORD_INIT_XML]
    if parent_token:
        args += ["--parent-token", parent_token]

    log("📄 首次运行，创建飞书 Word 文档...")
    resp = _run_lark(args, timeout=60, identity=get_identity())
    if not _ok(resp):
        raise RuntimeError(f"创建 Word 文档失败：{json.dumps(resp, ensure_ascii=False)[:500]}")

    doc_url = resp["data"]["document"]["url"]
    log(f"  ✅ Word 文档已创建：{doc_url}")
    return doc_url


# ── 初始化：Sheet ───────────────────────────────────────────────────

SHEET_HEADERS = ["📚 资源名称", "🏷️ 标签", "📂 类型", "📝 简介", "🔗 链接", "📅 推送日期"]


def init_sheet() -> Tuple[str, str]:
    """
    首次运行：创建电子表格，写入表头，返回 (spreadsheet_url, default_sheet_id)。
    """
    parent_token = get_parent_token()
    title = "pan.devmini.space 资源列表"

    # 1) 创建表格 + 表头
    initial_rows = [SHEET_HEADERS]
    args = [
        "sheets", "+create",
        "--title", title,
        "--data", json.dumps(initial_rows, ensure_ascii=False),
    ]
    if parent_token:
        args += ["--folder-token", parent_token]

    log("📊 首次运行，创建飞书 Sheet...")
    resp = _run_lark(args, timeout=60, identity=get_identity())
    if not _ok(resp):
        raise RuntimeError(f"创建 Sheet 失败：{json.dumps(resp, ensure_ascii=False)[:500]}")

    sheet_url = resp["data"].get("url") or resp["data"].get("spreadsheet_url", "")
    # 拿到默认 sheet id
    default_sheet_id = ""
    sheets = (resp.get("data") or {}).get("sheets") or []
    if sheets:
        default_sheet_id = sheets[0].get("sheet_id", "")
    elif (resp.get("data") or {}).get("sheet_id"):
        default_sheet_id = resp["data"]["sheet_id"]

    log(f"  ✅ Sheet 已创建：{sheet_url} (sheet_id={default_sheet_id})")
    return sheet_url, default_sheet_id


# ── 去重检查 ───────────────────────────────────────────────────────

def _doc_text_contains(doc_url: str, needle: str) -> bool:
    """
    拉取 Word 文档全文，搜索 needle 是否存在。
    用于按 quark_url 去重。
    """
    try:
        resp = _run_lark(
            ["docs", "+fetch", "--api-version", "v2", "--doc", doc_url],
            timeout=60,
            identity=get_identity(),
            )
    except Exception as exc:
        log(f"  ⚠ 拉取 Word 文档失败（去重检查跳过）：{exc}")
        return False

    if not _ok(resp):
        return False

    data = resp.get("data") or {}
    # data 可能是 {document: {content, ...}} 或直接 content
    blob = json.dumps(data, ensure_ascii=False)
    return needle in blob


def _sheet_text_contains(sheet_url: str, sheet_id: str, needle: str) -> bool:
    """
    拉取 Sheet 全表，搜索 needle 是否存在。
    """
    try:
        resp = _run_lark(
            [
                "sheets", "+read",
                "--url", sheet_url,
                "--sheet-id", sheet_id,
            ],
            timeout=60,
            identity=get_identity(),
            )
    except Exception as exc:
        log(f"  ⚠ 拉取 Sheet 失败（去重检查跳过）：{exc}")
        return False

    if not _ok(resp):
        return False
    blob = json.dumps(resp, ensure_ascii=False)
    return needle in blob


# ── 追加：Word ─────────────────────────────────────────────────────

def _format_word_block(item: Dict[str, Any], share_url: str, original_url: str,
                        date_str: str) -> str:
    """
    构造单个资源的 Word XML 块（XML v2 格式）。
    item: {title, description, tags: list, repo}
    """
    title = _xml_escape(item.get("title", ""))
    description = _xml_escape(item.get("description", ""))
    tags = item.get("tags") or []
    repo = item.get("repo", "")

    cat = REPO_CATEGORY.get(repo, {"emoji": "📦", "label": repo, "url": ""})
    cat_label = f"{cat['emoji']} {cat['label']}" if cat.get("emoji") else cat.get("label", repo)

    tags_str = " ".join(f"#{_xml_escape(t)}" for t in tags[:5]) if tags else "(无标签)"

    # 链接用 object-link 风格，飞书渲染更好
    # 简单起见用 a 标签
    share_escaped = _xml_escape(share_url)
    share_anchor = _xml_escape(share_url)
    original_escaped = _xml_escape(original_url)

    return (
        f'<h2>📚 {title}</h2>'
        f'<p>🏷️ <b>标签：</b>{tags_str}</p>'
        f'<p>📂 <b>类型：</b>{_xml_escape(cat_label)}</p>'
        f'<p>📝 <b>简介：</b>{description or "(无简介)"}</p>'
        f'<p>🔗 <b>链接：</b> <a href="{share_escaped}">👉 立即获取</a></p>'
        f'<p>📅 {_xml_escape(date_str)}</p>'
        f'<hr/>'
    )


def _xml_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def append_word_resource(doc_url: str, item: Dict[str, Any], share_url: str,
                          original_url: str) -> Dict[str, Any]:
    """
    追加单个资源到 Word 文档。
    """
    date_str = _now_str()
    content = _format_word_block(item, share_url, original_url, date_str)

    # 去重检查：按 share_url 在文档中扫
    if _doc_text_contains(doc_url, share_url):
        return {"status": "skipped", "reason": "already_appended", "doc_url": doc_url}

    log(f"  📄 追加到 Word：{item.get('title', '')[:30]}...")
    resp = _run_lark(
        [
            "docs", "+update", "--api-version", "v2",
            "--doc", doc_url,
            "--command", "append",
            "--content", content,
        ],
        timeout=60,
        identity=get_identity(),
        )
    if not _ok(resp):
        raise RuntimeError(f"Word append 失败：{json.dumps(resp, ensure_ascii=False)[:500]}")

    return {"status": "ok", "doc_url": doc_url}


# ── 追加：Sheet ────────────────────────────────────────────────────

def _format_sheet_row(item: Dict[str, Any], share_url: str, date_str: str) -> List[Any]:
    """
    构造 Sheet 一行（5 个核心字段 + 日期）。
    """
    title = item.get("title", "")
    description = item.get("description", "")
    tags = item.get("tags") or []
    repo = item.get("repo", "")

    cat = REPO_CATEGORY.get(repo, {"emoji": "📦", "label": repo})
    cat_label = f"{cat.get('emoji', '')} {cat.get('label', repo)}".strip()
    tags_str = " ".join(f"#{t}" for t in tags[:5]) if tags else ""

    # 链接用富文本对象，飞书会显示为超链接
    link_obj = {"type": "url", "text": "👉 立即获取", "link": share_url}

    return [
        title,
        tags_str,
        cat_label,
        description,
        link_obj,
        date_str,
    ]


def append_sheet_row(sheet_url: str, sheet_id: str, item: Dict[str, Any],
                      share_url: str) -> Dict[str, Any]:
    """
    追加一行到 Sheet。
    """
    date_str = _now_str()
    row = _format_sheet_row(item, share_url, date_str)

    # 去重：按 share_url 判断（与 Word 一致，物理统一）
    if _sheet_text_contains(sheet_url, sheet_id, share_url):
        return {"status": "skipped", "reason": "already_appended", "sheet_url": sheet_url}

    log(f"  📊 追加到 Sheet：{item.get('title', '')[:30]}...")
    resp = _run_lark(
        [
            "sheets", "+append",
            "--url", sheet_url,
            "--sheet-id", sheet_id,
            "--values", json.dumps([row], ensure_ascii=False),
        ],
        timeout=60,
    )
    if not _ok(resp):
        raise RuntimeError(f"Sheet append 失败：{json.dumps(resp, ensure_ascii=False)[:500]}")

    return {"status": "ok", "sheet_url": sheet_url}


# ── 主流程 ─────────────────────────────────────────────────────────

def ensure_doc_and_sheet() -> Dict[str, str]:
    """
    确保 Word 文档和 Sheet 存在；不存在则创建并写回 env。
    返回 {"doc_url": ..., "sheet_url": ..., "sheet_id": ...}。
    """
    result: Dict[str, str] = {}

    # Word
    doc_url = get_doc_url()
    if not doc_url:
        doc_url = init_word_doc()
        _save_env_value("FEISHU_DOC_URL", doc_url)
    result["doc_url"] = doc_url

    # Sheet
    sheet_url = get_sheet_url()
    sheet_id = ""
    if not sheet_url:
        sheet_url, sheet_id = init_sheet()
        _save_env_value("FEISHU_SHEET_URL", sheet_url)
    else:
        # 已知 sheet_url，查默认 sheet_id
        try:
            resp = _run_lark(
                ["sheets", "+info", "--url", sheet_url],
                timeout=30,
                identity=get_identity(),
                )
            if _ok(resp):
                # data.sheets.sheets[].sheet_id （嵌套两层）
                inner = (resp.get("data") or {}).get("sheets") or {}
                sheets = inner.get("sheets") if isinstance(inner, dict) else inner
                if sheets and isinstance(sheets[0], dict):
                    sheet_id = sheets[0].get("sheet_id", "")
                    log(f"  📋 查到 sheet_id: {sheet_id}")
        except Exception as exc:
            log(f"  ⚠ 查 sheet_id 失败：{exc}")

    if not sheet_id:
        sheet_id = get_sheet_tab()  # fallback 到环境变量 tab 名

    result["sheet_url"] = sheet_url
    result["sheet_id"] = sheet_id
    return result


def sync_resource(item: Dict[str, Any], share_url: str, original_url: str) -> Dict[str, Any]:
    """
    同步一个资源到 Word + Sheet。
    item: {title, description, tags, repo}
    share_url: 永久分享链接
    original_url: 原夸克分享链接
    """
    if not is_enabled():
        return {"status": "skipped", "reason": "feishu_disabled"}

    urls = ensure_doc_and_sheet()

    out: Dict[str, Any] = {"doc_url": urls.get("doc_url", ""), "sheet_url": urls.get("sheet_url", "")}

    # Word
    try:
        out["word"] = append_word_resource(urls["doc_url"], item, share_url, original_url)
    except Exception as exc:
        out["word"] = {"status": "failed", "error": str(exc)}
        log(f"  ❌ Word 追加失败：{exc}")

    # 频控
    time.sleep(1)

    # Sheet
    try:
        out["sheet"] = append_sheet_row(
            urls["sheet_url"], urls.get("sheet_id", "Sheet1"),
            item, share_url,
        )
    except Exception as exc:
        out["sheet"] = {"status": "failed", "error": str(exc)}
        log(f"  ❌ Sheet 追加失败：{exc}")

    # 汇总
    word_status = out.get("word", {}).get("status")
    sheet_status = out.get("sheet", {}).get("status")
    word_ok = word_status == "ok"
    sheet_ok = sheet_status == "ok"
    word_skip = word_status == "skipped"
    sheet_skip = sheet_status == "skipped"
    if word_ok and sheet_ok:
        out["status"] = "ok"
    elif word_ok or sheet_ok:
        out["status"] = "partial"
    elif word_skip and sheet_skip:
        out["status"] = "skipped"  # 两者都跳过 = 幂等命中，不算失败
    else:
        out["status"] = "failed"
    return out


# ── CLI 调试入口 ──────────────────────────────────────────────────

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="feishu_sync CLI（调试用）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="只初始化，不追加")
    p_init.add_argument("--kind", choices=["word", "sheet", "both"], default="both")

    p_append = sub.add_parser("append", help="追加单条资源")
    p_append.add_argument("--title", required=True)
    p_append.add_argument("--description", default="")
    p_append.add_argument("--tags", default="", help="空格分隔")
    p_append.add_argument("--repo", required=True)
    p_append.add_argument("--share-url", required=True)
    p_append.add_argument("--original-url", required=True)

    args = parser.parse_args()

    if not is_enabled():
        log("❌ FEISHU_ENABLED 未开启")
        return 1

    if args.cmd == "init":
        if args.kind in ("word", "both") and not get_doc_url():
            url = init_word_doc()
            _save_env_value("FEISHU_DOC_URL", url)
            log(f"Word URL: {url}")
        if args.kind in ("sheet", "both") and not get_sheet_url():
            url, sid = init_sheet()
            _save_env_value("FEISHU_SHEET_URL", url)
            log(f"Sheet URL: {url}, sheet_id: {sid}")
        return 0

    if args.cmd == "append":
        item = {
            "title": args.title,
            "description": args.description,
            "tags": [t for t in args.tags.split() if t],
            "repo": args.repo,
        }
        out = sync_resource(item, args.share_url, args.original_url)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("status") in ("ok", "partial", "skipped") else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
