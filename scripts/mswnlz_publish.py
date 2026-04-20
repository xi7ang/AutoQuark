#!/usr/bin/env python3
"""Publish Quark batch share results into GitHub content repos.

Usage examples:
  python mswnlz_publish.py --month 202603 --batch-json batch_share_results.json
  python mswnlz_publish.py --month 202603 --batch-json batch_share_results.json --skip-telegram --result-json publish_result.json --emit-json
"""

from __future__ import annotations

import argparse
import json
import os
import re



import pexpect
import subprocess
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from _common import get_mswnlz_root, load_env_files
from repo_classifier import classify_item
from _state import (
    BATCH_RUN_STATES_DIR,
    get_tg_notified_groups,
    is_repo_updated,
    is_tg_notified,
    load_batch_state,
    mark_repo_updated,
    mark_tg_notified,
    save_batch_state,
)

load_env_files()

MSWNLZ_ROOT = get_mswnlz_root(require=True)
GITHUB_OWNER = os.environ.get("MSWNLZ_GITHUB_OWNER", "").strip() or "YOUR_GITHUB_USERNAME"
SITE_SUFFIX = os.environ.get("SITE_SUFFIX", "")
SITE_URL = os.environ.get("SITE_URL", "https://your-site.example.com").strip() or "https://your-site.example.com"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUPS = [
    {"chat_id": os.environ.get("TG_GROUP_1_ID", "").strip(), "thread_id": os.environ.get("TG_GROUP_1_THREAD", "").strip()},
    {"chat_id": os.environ.get("TG_GROUP_2_ID", "").strip(), "thread_id": os.environ.get("TG_GROUP_2_THREAD", "").strip()},
    {"chat_id": os.environ.get("TG_GROUP_3_ID", "").strip(), "thread_id": os.environ.get("TG_GROUP_3_THREAD", "").strip()},
]
CACHE_PATH = Path(__file__).resolve().parent.parent / "references" / "mswnlz-repos-cache.json"

REPO_DISPLAY_NAMES = {
    "book": "📚 书籍资料",
    "movies": "🎬 影视资源",
    "AIknowledge": "🤖 AI知识",
    "curriculum": "🎓 课程教程",
    "edu-knowlege": "📖 教育知识",
    "healthy": "💪 健康养生",
    "self-media": "📱 自媒体",
    "cross-border": "🌍 跨境电商",
    "chinese-traditional": "🏮 传统文化",
    "tools": "🔧 工具软件",
    "games": "🎮 游戏资源",
}

REPO_BASE_TAGS = {
    "book": "#书籍资料",
    "movies": "#影视资源",
    "AIknowledge": "#AI知识",
    "curriculum": "#课程教程",
    "edu-knowlege": "#教育知识",
    "healthy": "#健康养生",
    "self-media": "#自媒体",
    "cross-border": "#跨境电商",
    "chinese-traditional": "#传统文化",
    "tools": "#工具软件",
    "games": "#游戏资源",
}


def sh(cmd: List[str], cwd: Path) -> str:
    p = subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True)
    return p.stdout.strip()


def run_noisy(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def ensure_clone(repo: str) -> None:
    repo_dir = MSWNLZ_ROOT / repo
    if repo_dir.exists():
        return
    MSWNLZ_ROOT.mkdir(parents=True, exist_ok=True)
    sh(["git", "clone", "--depth", "1", f"git@github.com:{GITHUB_OWNER}/{repo}.git"], cwd=MSWNLZ_ROOT)


def git_pull(repo_dir: Path) -> None:
    sh(["git", "checkout", "main"], cwd=repo_dir)
    sh(["git", "pull", "--rebase"], cwd=repo_dir)


def fetch_repo_descriptions() -> Dict[str, str]:
    url = f"https://api.github.com/users/{GITHUB_OWNER}/repos?per_page=100&sort=updated"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        repos = {r["name"]: (r.get("description") or "") for r in data}
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps({"updated_at": __import__("datetime").datetime.now().isoformat(), "repos": repos}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return repos
    except Exception as exc:
        if CACHE_PATH.exists():
            try:
                cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                repos = cached.get("repos") or {}
                if repos:
                    print(f"[WARN] GitHub repo 描述拉取失败，回退缓存：{exc}")
                    return repos
            except Exception:
                pass
        raise


def readme_insert_month(readme: str, month: str) -> str:
    lines = readme.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("# [") and "](" in line:
            if f"[{month}]({month}.md)" in line:
                return readme if readme.endswith("\n") else readme + "\n"
            lines[i] = f"# [{month}]({month}.md) " + line[2:].strip()
            return "\n".join(lines) + "\n"
    return f"# [{month}]({month}.md)\n\n" + (readme if readme.endswith("\n") else readme + "\n")


def append_items(month_file: Path, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    month_file.parent.mkdir(parents=True, exist_ok=True)
    existing = month_file.read_text(encoding="utf-8") if month_file.exists() else ""
    existing_lines = set(line.strip() for line in existing.splitlines() if line.strip())
    out = existing.rstrip("\n") + ("\n" if existing.strip() else "")
    added_items: List[Dict[str, Any]] = []
    for item in items:
        title = item["title"]
        url = item["share_url"]
        line = f"- {title}{SITE_SUFFIX} | {url}"
        if line in existing_lines:
            continue
        out += line + "\n"
        added_items.append(item)
    month_file.write_text(out, encoding="utf-8")
    return added_items


def make_commit_message(items: List[str]) -> str:
    return "\n".join(f"增加 {title}" for title in items)


def has_changes(repo_dir: Path) -> bool:
    return bool(run_noisy(["git", "status", "--short"], cwd=repo_dir).stdout.strip())


def shorten_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip(" ，,。；;：:-_/|") + "…"


def clean_resource_name(name: str) -> str:
    text = re.sub(r"https?://\S+", "", name)
    text = re.sub(r"[【】\[\]（）(){}]", " ", text)
    text = re.sub(r"(?i)v?\d+(?:\.\d+)+", " ", text)
    text = re.sub(r"(?i)4k|8k|1080p|720p|pdf|epub|mobi|azw3|app|apk|ipa", " ", text)
    text = re.sub(r"[_\-—|]+", " ", text)
    text = re.sub(r"[.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。；;：:-_/|")
    return text or name.strip()


# ── MiniMax AI 摘要生成 ──────────────────────────────────────────────

_AI_CACHE_PATH = Path(__file__).resolve().parent.parent / "references" / "ai_summary_cache.json"
_MINIMAX_API_URL = "https://api.minimax.chat/v1/chat/completions"   # OpenAI-compatible

# ── Worker result JSON 缓存（预加载，避免重复调 openclaw agent）─────────
_BATCH_CSV_DIR = Path(__file__).resolve().parent.parent.parent / "batch_csv"
_RESULT_JSON_CACHE: Dict[str, Dict[str, str]] = {}   # repo -> {title: description}


def _load_result_json_cache() -> None:
    """启动时预加载所有 *_result.json 到内存缓存."""
    global _RESULT_JSON_CACHE
    if _BATCH_CSV_DIR.is_dir():
        for p in _BATCH_CSV_DIR.glob("*_result.json"):
            repo = p.stem.replace("_result", "")   # "healthy_result" -> "healthy"
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    _RESULT_JSON_CACHE[repo] = {item["name"]: item["description"] for item in data if item.get("description")}
                elif isinstance(data, dict):
                    _RESULT_JSON_CACHE[repo] = data
            except Exception:
                pass


def _get_from_result_json(title: str, repo: str) -> Optional[str]:
    """查 result JSON 缓存（worker 生成的摘要）."""
    return _RESULT_JSON_CACHE.get(repo, {}).get(title)


# ── 预加载（模块级别执行）────────────────────────────────────────────
_load_result_json_cache()



def _load_ai_cache() -> Dict[str, str]:
    if _AI_CACHE_PATH.exists():
        try:
            return json.loads(_AI_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_ai_cache(cache: Dict[str, str]) -> None:
    try:
        _AI_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _AI_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def generate_ai_summary(title: str, repo: str) -> Optional[str]:
    """
    调用 OpenClaw 主 agent 生成贴合资源内容的50字中文简介。
    使用 subprocess + communicate() 避免 pexpect PTY 超时问题。
    失败时返回 None（外部调用方会降级到模板）。
    """
    cache = _load_ai_cache()
    cache_key = title

    # 第一级：内存缓存
    if cache_key in cache:
        return cache[cache_key]

    # 第二级：Worker result JSON（更稳定，直接复用）
    cached = _get_from_result_json(title, repo)
    if cached:
        # 同步回 ai_summary_cache，下次更快
        cache[cache_key] = cached
        _save_ai_cache(cache)
        return cached

    prompt_text = (
        "你是一个资源简介生成专家。请根据以下资源标题，生成一段最贴合内容本身的简介。\n"
        "要求：\n"
        "1. 直接描述这个资源是什么/做什么，要有人话\n"
        "2. 不超过50字\n"
        '3. 不要前缀如"这是一个"，直接描述内容\n'
        "4. 只输出简介正文，不要任何解释\n\n"
        f"资源标题：{title}"
    )

    output = ''
    try:
        proc = subprocess.run(
            ['/usr/bin/openclaw', 'agent', '--agent', 'main',
             '--message', prompt_text, '--json'],
            capture_output=True, text=True, timeout=35,
            cwd='/root/.openclaw/workspace',
        )
        output = proc.stdout
    except subprocess.TimeoutExpired:
        output = ''
    except Exception:
        output = ''

    if not output.strip():
        return None

    # 找 JSON 对象：第一个 { 开始，最后一个 } 结束
    raw = ''
    json_start = output.find('{')
    json_end = output.rfind('}') + 1
    if json_start >= 0 and json_end > json_start:
        json_str = output[json_start:json_end]
        try:
            response_data = json.loads(json_str)
            # 优先从 result.payloads 取，兼容只有 payloads 的简单响应
            payloads = response_data.get("payloads")
            if payloads is None:
                payloads = response_data.get("result", {}).get("payloads", [])
            raw = (payloads[0].get("text", "") if payloads else "").strip()
        except Exception:
            raw = ''

    if not raw:
        # 尝试直接返回非空原始输出（短文本情况）
        stripped = output.strip()
        if stripped and 3 <= len(stripped) <= 60:
            raw = stripped

    if not raw:
        return None

    # 从 thinking block 中提取中文摘要
    open_tag = "\x3c\x74\x68\x69\x6e\x6b\x3e"
    close_tag = "\x3c\x2f\x74\x68\x69\x6e\x6b\x3e"
    first_ob = raw.find(open_tag)
    first_cb = raw.find(close_tag, first_ob + 1) if first_ob >= 0 else -1

    if first_ob >= 0 and first_cb >= 0:
        inner = raw[first_ob + len(open_tag):first_cb]
        all_lines = [l.strip() for l in inner.strip().split("\n") if l.strip()]
        for line in reversed(all_lines):
            clean = re.sub(r'^["\'"\s\uff0e]+|["\'"\s\uff0e。]+$', '', line)
            chinese = len(re.findall(r'[\u4e00-\u9fff]', clean))
            if chinese >= 8 and 10 <= len(clean) <= 60:
                if any(w in clean for w in [
                    "我觉得", "我认为", "让我", "分析", "符合要求",
                    "检查", "字数", "数一下", "生成简介",
                ]):
                    continue
                cache[cache_key] = clean
                _save_ai_cache(cache)
                return clean

    if raw and 5 <= len(raw) <= 60:
        cache[cache_key] = raw
        _save_ai_cache(cache)
        return raw

    return None

def generate_resource_summary(name: str, repo: str) -> str:
    """优先调用 MiniMax AI 生成贴合内容的简介，失败时降级到模板。"""
    ai_result = generate_ai_summary(name, repo)
    if ai_result:
        return ai_result

    # ── 模板降级 ──
    cleaned = clean_resource_name(name)
    meta_parts = []
    version_match = re.search(r'v?[\d]+\.[\d.]+', cleaned)
    if version_match:
        meta_parts.append(version_match.group())
    if re.search(r'中文|英文|双语|简体|繁体', cleaned):
        lang = re.search(r'中文|英文|双语|简体|繁体', cleaned).group()
        if lang not in meta_parts:
            meta_parts.append(lang)
    if re.search(r'PC|安卓|Android|iOS|双版|双平台|PC+安卓', cleaned):
        plat = re.search(r'PC|安卓|Android|iOS|双版|双平台|PC\+安卓', cleaned).group()
        if plat not in meta_parts:
            meta_parts.append(plat)
    core = re.sub(r'[（(][^）)]*[）)]', '', cleaned)
    core = re.sub(r'v?[\d]+\.[\d.]+', '', core)
    core = re.sub(r'中文|英文|双语|简体|繁体|PC|安卓|Android|iOS|双版|双平台|PC\+安卓', '', core)
    core = shorten_text(core.strip(), 30).strip('.-_ ')
    meta_str = " ".join(meta_parts)
    repo_hints = {
        "book": f"{core}相关书籍资料。",
        "movies": f"{core}影视作品。",
        "AIknowledge": f"{core}，AI相关资料。",
        "curriculum": f"{core}课程教程。",
        "edu-knowlege": f"{core}教育知识内容。",
        "healthy": f"{core}健康养生资料。",
        "self-media": f"{core}自媒体运营资料。",
        "cross-border": f"{core}跨境电商内容。",
        "chinese-traditional": f"{core}传统文化资料。",
        "tools": f"{core}工具软件资源。",
        "games": f"{core}游戏资源。",
    }
    hint = repo_hints.get(repo, f"{core}精选资料。")
    if meta_str:
        return shorten_text(f"{hint} 含{meta_str}", 50)
    return shorten_text(hint, 50)


def generate_resource_tags(name: str, repo: str) -> List[str]:
    tags: List[str] = []
    base_tag = REPO_BASE_TAGS.get(repo)
    if base_tag:
        tags.append(base_tag)

    keyword_tags = [
        (r"微博", "#微博资源"),
        (r"模块|插件|扩展|内置模块", "#模块插件"),
        (r"课程|教程|训练营|学习", "#课程学习"),
        (r"书|书籍|杂志|电子书|小说|出版", "#书籍合集"),
        (r"电影|剧|纪录片|演唱会|影视", "#影视合集"),
        (r"AI|人工智能|提示词|大模型|GPT|Claude|Gemini", "#AI资源"),
        (r"健康|养生|营养|健身", "#健康生活"),
        (r"跨境|外贸|亚马逊|独立站", "#跨境运营"),
        (r"教育|学而思|猿辅导|试卷|教辅", "#教育资料"),
        (r"传统文化|国学|古籍|诗词|历史", "#传统文化"),
        (r"源码|代码|开发|编程|文档", "#技术资料"),
        (r"客户端|软件|工具|APP|app|apk|ipa", "#效率工具"),
    ]
    for pattern, tag in keyword_tags:
        if re.search(pattern, name, re.IGNORECASE) and tag not in tags:
            tags.append(tag)
        if len(tags) >= 3:
            return tags[:3]

    fallback = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", clean_resource_name(name))
    if fallback:
        fallback_tag = "#" + shorten_text(fallback, 10).replace("…", "")
        if fallback_tag not in tags:
            tags.append(fallback_tag)
    for fallback_tag in ("#精选资源", "#资源更新"):
        if len(tags) >= 3:
            break
        if fallback_tag not in tags:
            tags.append(fallback_tag)
    return tags[:3]


BOT_START_URL_BASE = os.environ.get(
    "BOT_START_URL_BASE",
    "https://t.me/YOUR_BOT_USERNAME?start="
)


def build_single_caption(item: Dict[str, Any], repo: str) -> str:
    """生成单个资源的 Telegram 专辑 caption 格式：

    {title} #{tag1} #{tag2} #{tag3} 资源简介单段（80字）

    💾 获取资源：👉 点我获取{title}👈
    （👉 点我获取{title}👈 整段是超链接，href 指向 Bot start_link）
    """
    title = item["title"]
    tags = generate_resource_tags(title, repo)
    summary = generate_resource_summary(title, repo)

    tag_str = " ".join(tags[:3])

    # Bot start link：用标题做 seed，URL encode
    seed = urllib.parse.quote(title[:30])
    bot_link = f"{BOT_START_URL_BASE}{seed}"

    caption = (
        f"{title} {tag_str}\n"
        f"{summary}\n\n"
        f"💾 获取资源：<a href=\"{bot_link}\">👉 点我获取{title}👈</a>"
    )
    return caption


def build_notification_text(by_repo: Dict[str, List[Dict[str, Any]]], batch_folder: str, title: str) -> str:
    total = 0
    lines = [title, ""]
    for repo, items in by_repo.items():
        if not items:
            continue
        for item in items:
            caption = build_single_caption(item, repo)
            lines.append(caption)
            lines.append("")
            total += 1
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend(["", "=" * 40, f"🌐 资料总站：{SITE_URL}"])
    if batch_folder:
        lines.append(f"📂 批次文件夹：{batch_folder}")
    lines.append(f"📊 共 {total} 项新增资源")
    return "\n".join(lines)


def split_telegram_text(text: str, limit: int = 3500) -> List[str]:
    if len(text) <= limit:
        return [text]
    lines = text.splitlines()
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current).strip())
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current).strip())
    return chunks


def send_telegram_group_notification(
    by_repo: Dict[str, List[Dict[str, Any]]], batch_folder: str, batch_id: str = ""
) -> Dict[str, Any]:
    if not TELEGRAM_BOT_TOKEN or not by_repo:
        return {"attempted": False, "sent": False, "reason": "missing_token_or_no_items"}

    # ── 幂等：跳过本批次已推送过的群组 ─────────────────────────────────
    already_notified = set(get_tg_notified_groups(batch_id)) if batch_id else set()
    groups = [g for g in TELEGRAM_GROUPS if g["chat_id"] and g["chat_id"] not in already_notified]

    if not groups:
        print("[SKIP] 所有群组均已推送过，跳过 Telegram 通知")
        return {"attempted": True, "sent": False, "reason": "all_already_sent", "groups": list(already_notified)}

    text = build_notification_text(by_repo, batch_folder, "📝 资源更新")
    chunks = split_telegram_text(text)
    sent_groups = []
    errors = []

    for group in groups:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        sent_ok = True
        for chunk in chunks:
            data = {"chat_id": group["chat_id"], "text": chunk}
            if group["thread_id"]:
                data["message_thread_id"] = group["thread_id"]
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode("utf-8"), method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    if not result.get("ok"):
                        sent_ok = False
                        errors.append({"group": group["chat_id"], "error": result.get("description")})
                        break
            except Exception as exc:
                sent_ok = False
                errors.append({"group": group["chat_id"], "error": str(exc)})
                break
        if sent_ok:
            # ── 幂等记录：成功后写入状态文件 ───────────────────────────
            if batch_id:
                mark_tg_notified(batch_id, group["chat_id"], chunks=len(chunks))
            sent_groups.append(group["chat_id"])
            print(f"[TG] 发送到群组 {group['chat_id']} ✅")

    return {
        "attempted": True,
        "sent": bool(sent_groups),
        "groups": sent_groups,
        "errors": errors,
        "chunks": len(chunks),
        "already_sent": list(already_notified),
    }


def generate_quark_group_message(by_repo: Dict[str, List[Dict[str, Any]]], batch_folder: str) -> str:
    return build_notification_text(by_repo, batch_folder, "📦 资源更新通知")


def trigger_site_rebuild() -> Dict[str, Any]:
    script_dir = Path(__file__).parent
    trigger_script = script_dir / "trigger_site_rebuild.sh"
    if not trigger_script.exists():
        print("[WARN] trigger_site_rebuild.sh 不存在，跳过网站更新")
        return {"attempted": False, "triggered": False, "reason": "missing_script"}
    try:
        result = subprocess.run(["bash", str(trigger_script)], cwd=str(script_dir), capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            print("[OK] 网站更新已完成")
            if result.stdout.strip():
                print(result.stdout.strip())
            return {"attempted": True, "triggered": True, "stdout": result.stdout.strip()}
        detail = result.stderr.strip() or result.stdout.strip()
        print(f"[WARN] 网站更新触发失败: {detail}")
        return {"attempted": True, "triggered": False, "error": detail}
    except Exception as exc:
        print(f"[WARN] 网站更新触发异常: {exc}")
        return {"attempted": True, "triggered": False, "error": str(exc)}


def build_repo_urls(repo: str, month: str) -> Dict[str, str]:
    repo_url = f"https://github.com/{GITHUB_OWNER}/{repo}"
    month_md_url = f"{repo_url}/blob/main/{month}.md"
    site_repo_url = f"https://openaitx.github.io/view.html?user={GITHUB_OWNER}&project={repo}&lang=zh-CN"
    return {"repo_url": repo_url, "month_md_url": month_md_url, "content_url": site_repo_url}


def build_result_item(item: Dict[str, Any], repo: str, month: str, status: str) -> Dict[str, Any]:
    urls = build_repo_urls(repo, month)
    return {
        "id": item.get("id"),
        "title": item["title"],
        "repo": repo,
        "repo_path": f"{repo}/{month}.md",
        "repo_url": urls["repo_url"],
        "month_md_url": urls["month_md_url"],
        "content_url": urls["content_url"],
        "share_url": item.get("share_url"),
        "status": status,
        "classification": item.get("classification"),
    }


def load_item_hints(items_json_path: str) -> tuple[Dict[str, str], Dict[str, str], Dict[str, Dict[str, Any]]]:
    if not items_json_path:
        return {}, {}, {}
    payload = json.loads(Path(items_json_path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        items = payload.get("items") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    by_title: Dict[str, str] = {}
    by_url: Dict[str, str] = {}
    hints_by_id: Dict[str, Dict[str, Any]] = {}
    for entry in items:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("id")
        if item_id is not None:
            item_id = str(item_id)
            title = (entry.get("title") or entry.get("name") or "").strip()
            if title and title not in by_title:
                by_title[title] = item_id
            url = (entry.get("url") or entry.get("source_url") or entry.get("input_url") or "").strip()
            if url and url not in by_url:
                by_url[url] = item_id
            hints_by_id[item_id] = {
                "id": item_id,
                "title": title,
                "repo": entry.get("repo"),
                "category": entry.get("category"),
                "tags": entry.get("tags") or [],
                "source_url": url,
            }
    return by_title, by_url, hints_by_id


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True)
    ap.add_argument("--batch-json", required=True)
    ap.add_argument("--items-json")
    ap.add_argument("--skip-telegram", action="store_true")
    ap.add_argument("--skip-push", action="store_true")
    ap.add_argument("--skip-rebuild", action="store_true")
    ap.add_argument("--emit-json", action="store_true")
    ap.add_argument("--result-json")
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    batch = json.loads(Path(args.batch_json).read_text(encoding="utf-8"))
    share_results = batch.get("share_results") or []
    batch_folder = batch.get("batch_folder_name", "")
    id_by_title, id_by_url, hints_by_id = load_item_hints(args.items_json)

    repo_desc = fetch_repo_descriptions()

    by_repo: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in share_results:
        name = (entry.get("title") or entry.get("name") or "").strip()
        url = (entry.get("share_url") or "").strip()
        if not name or not url:
            continue
        source_url = (entry.get("source_url") or entry.get("input_url") or "").strip()
        item_id = entry.get("id")
        if item_id is None:
            item_id = id_by_url.get(source_url) or id_by_title.get(name)
        item_id_str = str(item_id) if item_id is not None else ""
        item_hint = hints_by_id.get(item_id_str, {}) if item_id_str else {}
        classification = classify_item(name, repo_desc, item_hint=item_hint)
        repo = classification["repo"]
        by_repo[repo].append(
            {
                "id": item_id,
                "title": name,
                "share_url": url,
                "source_url": source_url,
                "raw": entry,
                "classification": classification,
            }
        )

    updated_repos: List[str] = []
    added_by_repo: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    result_items: List[Dict[str, Any]] = []
    repo_commits: Dict[str, Dict[str, Any]] = {}
    any_push = False

    batch_id = batch.get("batch_id", "")

    for repo, items in by_repo.items():
        # ── 幂等：跳过本批次已更新过的仓库 ───────────────────────────
        if batch_id and is_repo_updated(batch_id, repo):
            log_msg = f"[恢复] 跳过已更新仓库：{repo}"
            print(log_msg)
            # 从状态文件恢复已推送的 commit hash
            state = load_batch_state(batch_id)
            saved_commit = state.get("_repo_commits", {}).get(repo, {})
            if saved_commit:
                repo_commits[repo] = saved_commit
            for item in items:
                result_items.append(build_result_item(item, repo, args.month, "skipped"))
            continue

        ensure_clone(repo)
        repo_dir = MSWNLZ_ROOT / repo
        git_pull(repo_dir)

        month_file = repo_dir / f"{args.month}.md"
        added_items = append_items(month_file, items)

        readme_path = repo_dir / "README.md"
        readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
        readme_path.write_text(readme_insert_month(readme, args.month), encoding="utf-8")

        sh(["git", "add", f"{args.month}.md", "README.md"], cwd=repo_dir)
        if not has_changes(repo_dir):
            print(f"[SKIP] {repo}: 没有新增内容")
            existing_titles = {it['title'] for it in added_items}
            for item in items:
                result_items.append(build_result_item(item, repo, args.month, "skipped" if item['title'] not in existing_titles else "published"))
            if batch_id:
                mark_repo_updated(batch_id, repo)
            continue

        msg_items = added_items or items
        msg = make_commit_message([item["title"] for item in msg_items])
        sh(["git", "commit", "-m", msg], cwd=repo_dir)
        commit_hash = sh(["git", "rev-parse", "HEAD"], cwd=repo_dir)
        pushed = False
        if not args.skip_push:
            sh(["git", "push", "origin", "main"], cwd=repo_dir)
            pushed = True
            any_push = True

        repo_commits[repo] = {"commit": commit_hash, "pushed": pushed}

        if batch_id:
            mark_repo_updated(batch_id, repo)
            # 保存 commit hash 以便恢复
            state = load_batch_state(batch_id)
            state.setdefault("_repo_commits", {})[repo] = repo_commits[repo]
            save_batch_state(batch_id, state)

        if added_items:
            updated_repos.append(repo)
            added_by_repo[repo].extend(added_items)
        for item in items:
            status = "published" if item in added_items else "skipped"
            result_items.append(build_result_item(item, repo, args.month, status))
        print(f"[OK] {repo}: 新增 {len(added_items)} 项{'，已 push' if pushed else '，未 push'}")

    quark_msg = generate_quark_group_message(added_by_repo, batch_folder)
    quark_msg_file = Path(args.batch_json).parent / "quark_group_message.txt"
    quark_msg_file.write_text(quark_msg, encoding="utf-8")
    print(f"\n[夸克群组消息] 已保存到: {quark_msg_file}")

    telegram_result = {"attempted": False, "sent": False, "reason": "skip_telegram"}
    if updated_repos and not args.skip_telegram:
        print("\n[TG] 发送群组明细通知...")
        telegram_result = send_telegram_group_notification(added_by_repo, batch_folder, batch_id=batch_id)

    rebuild_result = {"attempted": False, "triggered": False, "reason": "skip_rebuild"}
    if updated_repos and not args.skip_rebuild:
        print("\n[网站] 触发站点重建...")
        rebuild_result = trigger_site_rebuild()

    result = {
        "month": args.month,
        "batch_folder_name": batch_folder,
        "updated_repos": updated_repos,
        "items": result_items,
        "commit": {
            "created": bool(repo_commits),
            "pushed": any_push,
            "repos": repo_commits,
        },
        "telegram": telegram_result,
        "rebuild": rebuild_result,
        "quark_group_message_file": str(quark_msg_file),
    }

    if args.result_json:
        result_path = Path(args.result_json)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.emit_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
