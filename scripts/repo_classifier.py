#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

VALID_REPOS = (
    "book",
    "movies",
    "AIknowledge",
    "curriculum",
    "edu-knowlege",
    "healthy",
    "self-media",
    "cross-border",
    "chinese-traditional",
    "tools",
    "games",
)

DEFAULT_REPO = "curriculum"

CATEGORY_REPO_MAP = {
    "book": "book",
    "books": "book",
    "ebook": "book",
    "ebooks": "book",
    "magazine": "book",
    "movie": "movies",
    "movies": "movies",
    "tv": "movies",
    "video": "movies",
    "anime": "movies",
    "drama": "movies",
    "film": "movies",
    "course": "curriculum",
    "courses": "curriculum",
    "curriculum": "curriculum",
    "tutorial": "curriculum",
    "tutorials": "curriculum",
    "lesson": "curriculum",
    "lessons": "curriculum",
    "education": "edu-knowlege",
    "edu": "edu-knowlege",
    "healthy": "healthy",
    "health": "healthy",
    "fitness": "healthy",
    "self-media": "self-media",
    "selfmedia": "self-media",
    "media": "self-media",
    "cross-border": "cross-border",
    "crossborder": "cross-border",
    "ai": "AIknowledge",
    "aigc": "AIknowledge",
    "llm": "AIknowledge",
    "agent": "AIknowledge",
    "tool": "tools",
    "tools": "tools",
    "software": "tools",
    "app": "tools",
    "culture": "chinese-traditional",
    "traditional": "chinese-traditional",
    "game": "games",
    "games": "games",
    "gaming": "games",
}

REPO_RULES: Dict[str, Dict[str, List[str]]] = {
    "book": {
        "strong": ["电子书", "书籍", "书单", "杂志", "小说", "出版社", "出版", "epub", "mobi", "azw3", "全集", "套装", "合集", "文学", "名著", "创作", "文集"],
        "medium": ["作者", "阅读", "图书", "pdf书", "pdf"],
        "weak": ["纸质", "珍藏版"],
        "negative": ["课程", "教程", "训练营", "工作流", "workflow", "coze", "扣子", "智能体", "agent", "软件", "插件", "apk", "游戏"],
    },
    "movies": {
        "strong": ["影视", "电影", "纪录片", "演唱会", "电视剧", "动画", "动漫", "番剧", "剧场版", "片源"],
        "medium": ["美剧", "韩剧", "日剧", "短剧", "蓝光", "bluray", "web-dl"],
        "weak": ["海报", "剧照", "片单"],
        "negative": ["教程", "课程", "书籍", "电子书"],
    },
    "AIknowledge": {
        "strong": ["ai", "aigc", "chatgpt", "gpt", "claude", "gemini", "llm", "大模型", "提示词", "智能体", "agent", "agents", "coze", "扣子", "工作流", "workflow", "mcp", "rag", "dify", "comfyui", "midjourney", "stable diffusion"],
        "medium": ["自动化", "知识库", "机器人", "prompt", "prompts", "openai", "anthropic"],
        "weak": ["模型", "推理", "生成式"],
        "negative": ["出版社", "杂志", "小说", "电影", "纪录片"],
    },
    "curriculum": {
        "strong": ["课程", "教程", "训练营", "实战", "刷题", "入门到精通", "教学视频", "系统课", "国考", "省考", "事业单位", "行测", "申论", "公考", "软考", "真题", "试卷", "习题", "考公", "考编", "超大杯", "系统班", "笔试", "面试", "备考", "付费课程", "视频课", "系统班"],
        "medium": ["学习", "讲解", "作业", "案例课", "训练课", "带练", "备考", "冲刺", "押题", "答案", "解析"],
        "weak": ["复盘", "笔记", "课件"],
        "negative": ["电子书", "杂志", "小说", "电影", "纪录片", "游戏", "安卓手机游戏", "Steam移植", "完整版", "mod版", "MOD菜单", "中文版", "汉化版", "apk"],
    },
    "edu-knowlege": {
        "strong": ["教育", "幼儿园", "小学", "初中", "高中", "中学", "学而思", "猿辅导", "教辅", "试卷"],
        "medium": ["备课", "教案", "奥数", "升学", "学科"],
        "weak": ["课堂", "教材"],
        "negative": ["跨境", "自媒体"],
    },
    "healthy": {
        "strong": ["健康", "健身", "锻炼", "饮食", "营养", "睡眠", "养生", "减脂", "跑步", "瑜伽"],
        "medium": ["食谱", "康复", "运动", "体态"],
        "weak": ["习惯", "身心"],
        "negative": ["电影", "课程"],
    },
    "self-media": {
        "strong": ["自媒体", "流量", "拉新", "获客", "转化", "变现", "选题", "私域", "短视频", "内容运营", "小红书", "公众号"],
        "medium": ["爆款", "涨粉", "运营", "口播", "脚本"],
        "weak": ["营销", "传播"],
        "negative": ["跨境", "亚马逊"],
    },
    "cross-border": {
        "strong": ["跨境", "亚马逊", "tiktok", "外贸", "独立站", "temu", "shopify", "etsy", "选品"],
        "medium": ["广告投放", "站外", "listing", "物流", "shopee"],
        "weak": ["电商", "出海"],
        "negative": ["教育", "教辅"],
    },
    "chinese-traditional": {
        "strong": ["传统文化", "国学", "古籍", "诗词", "易经", "道家", "儒家", "佛学", "中医古籍"],
        "medium": ["文言文", "经典", "礼仪"],
        "weak": ["文化", "典籍"],
        "negative": ["软件", "插件", "安卓手机游戏", "Steam移植", "完整版", "mod版", "MOD菜单", "破解版", "中文版", "汉化版", "apk", "DLC", "xg器", "最新安卓版"],
    },
    "tools": {
        "strong": ["软件", "工具", "插件", "扩展", "扩充包", "解锁版", "专业版", "激活版", "绿色版", "便携版", "浏览器", "编辑器", "客户端", "模块", "内置模块", "ipa", "脚本", "模版", "ppt模板", "简历模板", "Word模板", "PPT模板"],
        "medium": ["安装包", "桌面端", "移动端", "tapscanner", "效率工具", "版本", "更新版"],
        "weak": [],
        "negative": ["电影", "纪录片", "小说", "安卓手机游戏", "Steam移植", "完整版"],
    },
    "games": {
        "strong": ["安卓手机游戏", "Steam移植", "完整版", "mod版", "MOD菜单", "中文版", "汉化版", "网飞版", "最新安卓版", "全DLC", "破解版", "修改器", "攻略", "作弊器", "游戏合集", "游戏存档", "游戏补丁", "游戏mod", "汉化补丁", "xg器", "免安装", "移植版", "豪华版", "终极版", "免激活", "联机版", "单机版", "DLC", "steam", "switch", "ps5", "ps4", "xbox", "塞尔达", "原神", "王者", "吃鸡", "我的世界", "mc", "lol", "csgo", "cs2", "dota2", "永劫无间", "黑神话", "悟空", "GTA", "艾尔登法环", "博德之门", "最终幻想", "宝可梦", "口袋妖怪", "游戏王", "赛博朋克", "rpg", "fps", "moba", "roguelike", "galgame", "RPG", "FPS", "MOBA", "Build", "三国志", "三国志姜维传", "幻兽帕鲁", "塞尔达传说", "荒野大镖客", "只狼", "艾尔登法环", "霍格沃兹之遗", "仁王", "真三国无双", "刺客信条", "使命召唤", "古墓丽影", "怪物猎人", "生化危机", "寂静岭", "星露谷", "空洞骑士", "死亡细胞", "博德之门", "赛博朋克2077", "战地", "帝国时代", "三国志8", "三国志9", "大航海时代", "星露谷物语", "雨中冒险", "泰拉瑞亚", "我的世界", "mc", "米塔", "吸血鬼幸存者", "杀戮尖塔", "八方旅人", "风来之国", "女神异闻录", "勇者斗恶龙", "最终幻想", "轨迹系列", "星之卡比", "火焰纹章", "动物森友会", "集合啦", "哈迪斯", "小丑牌", "以撒的结合", "RPG", "SLG", "ACT", "AVG", "STG", "Puzzle", "休闲", "养成", "模拟经营", "沙盒", "像素", "横版", "动作冒险", "策略战棋", "解谜", "恐怖生存", "末日", "内置", "内置金手指", "内置菜单", "直装版", "免谷歌", "免加速器", "完整版apk", "安卓最新版", "安卓手机版", "安卓版", "手机版", "手机apk", "单机+联机", "apk+pc"],
        "medium": ["游戏视频", "游戏直播", "游戏赛事", "游戏外挂", "游戏辅助", "游戏语音包", "游戏音乐", "游戏美术", "游戏CG", "游戏立绘", "游戏配音", "娱乐", "闯关", "通关", "通关存档", "mod教程"],
        "weak": [],
        "negative": ["pdf", "PDF", "系统班", "付费课", "电子教材", "真题卷"],
    },
}

WEIGHTS = {"strong": 6, "medium": 3, "weak": 1, "negative": -4}
SOURCE_BONUS = {"title": 0, "tags": -1, "category": 0, "repo_desc": 1}


def _normalize_repo(value: Any) -> str:
    repo = str(value or "").strip()
    return repo if repo in VALID_REPOS else ""


def _normalize_category(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _normalize_tags(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _ascii_keyword(keyword: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9 .+\-]+", keyword or ""))


def _keyword_in_text(keyword: str, text: str) -> bool:
    if not keyword or not text:
        return False
    if _ascii_keyword(keyword):
        pattern = r"(?<![A-Za-z0-9])" + re.escape(keyword) + r"(?![A-Za-z0-9])"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    return keyword.lower() in text.lower()


def _add_match(matches: List[Dict[str, Any]], repo: str, keyword: str, weight: int, source: str) -> None:
    matches.append({"repo": repo, "keyword": keyword, "weight": weight, "source": source})


def _confidence(top_score: int, second_score: int, overridden: bool) -> str:
    if overridden:
        return "override"
    gap = top_score - second_score
    if top_score >= 10 and gap >= 4:
        return "high"
    if top_score >= 5 and gap >= 2:
        return "medium"
    return "low"


def classify_item(name: str, repo_desc: Dict[str, str], item_hint: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item_hint = item_hint or {}
    title = str(name or "").strip()
    manual_repo_raw = str(item_hint.get("repo") or "").strip()
    manual_repo = _normalize_repo(manual_repo_raw)
    tags = _normalize_tags(item_hint.get("tags"))
    category = _normalize_category(item_hint.get("category"))

    if manual_repo:
        return {
            "repo": manual_repo,
            "score": 999,
            "confidence": "override",
            "overridden": True,
            "reason": f"使用 item.repo 人工覆盖：{manual_repo}",
            "matched": [{"repo": manual_repo, "keyword": manual_repo, "weight": 999, "source": "item.repo"}],
            "scores": {repo: (999 if repo == manual_repo else 0) for repo in VALID_REPOS},
            "top_candidates": [{"repo": manual_repo, "score": 999}],
            "warnings": [],
        }

    warnings: List[str] = []
    if manual_repo_raw and not manual_repo:
        warnings.append(f"忽略无效 item.repo：{manual_repo_raw}")

    scores = {repo: 0 for repo in VALID_REPOS}
    matched: List[Dict[str, Any]] = []

    mapped_repo = CATEGORY_REPO_MAP.get(category)
    if mapped_repo:
        scores[mapped_repo] += 7
        _add_match(matched, mapped_repo, category, 7, "category")

    tag_text = " ".join(tags)
    for repo, rules in REPO_RULES.items():
        desc_text = str(repo_desc.get(repo, "") or "")
        for level in ("strong", "medium", "weak", "negative"):
            base_weight = WEIGHTS[level]
            for keyword in rules.get(level, []):
                if _keyword_in_text(keyword, title):
                    scores[repo] += base_weight
                    _add_match(matched, repo, keyword, base_weight + SOURCE_BONUS["title"], "title")
                if tag_text and _keyword_in_text(keyword, tag_text):
                    tag_weight = base_weight + SOURCE_BONUS["tags"]
                    if tag_weight:
                        scores[repo] += tag_weight
                        _add_match(matched, repo, keyword, tag_weight, "tags")
                if desc_text and _keyword_in_text(keyword, title) and _keyword_in_text(keyword, desc_text):
                    desc_weight = SOURCE_BONUS["repo_desc"]
                    if desc_weight:
                        scores[repo] += desc_weight
                        _add_match(matched, repo, keyword, desc_weight, "repo_desc")

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], list(VALID_REPOS).index(kv[0])))
    top_repo, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    if top_score <= 0:
        top_repo = DEFAULT_REPO
        top_score = 0
        reason = f"未命中足够强的分类信号，回退默认仓库：{DEFAULT_REPO}"
    else:
        reason = f"按加权得分选择最高仓库：{top_repo}（score={top_score}，second={second_score}）"

    return {
        "repo": top_repo,
        "score": top_score,
        "confidence": _confidence(top_score, second_score, overridden=False),
        "overridden": False,
        "reason": reason,
        "matched": [m for m in matched if m["repo"] == top_repo],
        "scores": scores,
        "top_candidates": [{"repo": repo, "score": score} for repo, score in ranked[:3]],
        "warnings": warnings,
    }


__all__ = ["VALID_REPOS", "DEFAULT_REPO", "classify_item"]
