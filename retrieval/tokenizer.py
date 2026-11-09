# -*- coding: utf-8 -*-
"""jieba 分词 + 同义词扩展。"""
from __future__ import annotations

import re
from typing import Iterable

# jieba 是运行时依赖，没装也能降级（用 re 正则分词）
try:
    import jieba

    jieba.setLogLevel(20)  # 关闭 jieba 初始化日志
    _JIEBA_OK = True
except ImportError:
    _JIEBA_OK = False


# 基本中文停用词（检索价值低）
STOPWORDS = {
    "的", "了", "和", "是", "在", "有", "我", "你", "他", "她", "它",
    "这", "那", "一个", "一些", "什么", "怎么", "如何", "为什么", "哪",
    "到", "会", "要", "让", "给", "从", "往", "对", "为", "与",
    "能", "可以", "应该", "需要", "请", "帮", "帮我", "测试",
}

# 英文停用词
EN_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "for", "and", "or", "not", "but",
    "how", "what", "why", "when", "where", "does", "do", "did",
    "please", "help", "test", "testing",
}


def _tokenize_cn(text: str) -> list[str]:
    """中文分词。优先 jieba，降级用正则切单字+英文词。"""
    if not text:
        return []
    if _JIEBA_OK:
        return list(jieba.cut(text))
    # 降级：英文词走 re.findall，中文走单字
    english_words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,}", text)
    cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return english_words + cn_chars


def tokenize(text: str) -> list[str]:
    """完整分词管道：分词 → 去停用 → 小写 → 去空串。"""
    raw = _tokenize_cn(text)
    out = []
    for t in raw:
        t = t.strip().lower()
        if not t or t in STOPWORDS or t in EN_STOPWORDS:
            continue
        # 去纯标点
        if len(t) <= 1 and not re.match(r"[a-z0-9]", t):
            continue
        out.append(t)
    return out


def expand_with_aliases(tokens: Iterable[str], aliases: dict[str, list[str]]) -> list[str]:
    """
    把 tokens 里每个词查同义词表，扩展成 (term, alias1, alias2, ...)
    别名也分词。注意：不做去重，上层再聚合。
    """
    result = []
    for tok in tokens:
        result.append(tok)
        # 精确匹配同义词 key
        if tok in aliases:
            result.extend(aliases[tok])
            continue
        # 子串匹配：用户说 "ota升级" 但同义词里只有 "ota" 和 "升级"
        for term, alist in aliases.items():
            if term in tok or tok in term:
                result.extend(alist)
    # 去空串 + 小写
    return [t.strip().lower() for t in result if t and t.strip()]


def build_fts_query(tokens: list[str]) -> str:
    """
    把 token 列表拼成 FTS5 MATCH 查询字符串。
    - 英文 / 数字 / 长中文词用引号精确匹配
    - 短中文词（单/双字）用双引号防分词
    - 词间用 AND（默认语义）
    - 用 OR 让任何一个命中都行，召回率优先
    """
    if not tokens:
        return ""
    cleaned = []
    for t in tokens:
        t = t.strip().lower()
        if not t:
            continue
        # 含非中文的，原样包引号
        if re.search(r"[a-z0-9]", t):
            cleaned.append(f'"{t}"')
        else:
            cleaned.append(f'"{t}"')
    # 用 OR 合并 → 召回率优先
    return " OR ".join(cleaned)


# ============ 目录路由 ============

def route_catalog(tokens: list[str], module_index: list[dict]) -> list[str]:
    """
    从 tokens 里匹配 module_index 的 module 名，返回可能的 catalog_lv1 列表。
    返回的是 ['通用功能', '音视频能力'] 这种。
    """
    if not tokens or not module_index:
        return []
    token_set = set(tokens)
    hit_modules = []
    for entry in module_index:
        name = entry["module"]
        # 简单策略：module 名中的关键词在 token 里命中
        # 比如 "通用功能/音视频能力/OTA升级" → 关键词 "ota", "升级"
        keywords = set(re.findall(r"[a-zA-Z]+|[\u4e00-\u9fff]{2,}", name.lower()))
        if keywords & token_set:
            hit_modules.append(name)
        # 子串兜底：token 直接在 module 名里
        elif any(t in name.lower() for t in tokens if len(t) >= 2):
            hit_modules.append(name)
    return hit_modules
