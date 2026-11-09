# -*- coding: utf-8 -*-
"""三路 hybrid 召回：FTS5 BM25 + catalog LIKE + 向量 FAISS，加权融合。"""
from __future__ import annotations

import math
import sqlite3
from typing import Any

from config import RETR_MIN_SCORE, RETR_PRIORITY_SCORE, RETR_TOPK_CATALOG, RETR_TOPK_FTS, RETR_WEIGHTS
from retrieval.db import load_aliases
from retrieval.tokenizer import build_fts_query, expand_with_aliases, route_catalog, tokenize


def _bm25(conn: sqlite3.Connection, fts_query: str, topk: int) -> list[dict[str, Any]]:
    if not fts_query:
        return []
    sql = """
        SELECT c.case_id, c.case_name, c.priority, c.catalog_lv1, c.catalog_lv2,
               c.catalog_lv3, c.catalog_path, c.precondition, c.steps, c.expected,
               c.tags, c.last_result, bm25(cases_fts) AS fts_score, rank
        FROM cases_fts JOIN cases c ON c.case_id = cases_fts.rowid
        WHERE cases_fts MATCH ? ORDER BY fts_score ASC LIMIT ?
    """
    return [dict(r) for r in conn.execute(sql, (fts_query, topk)).fetchall()]


def _catalog_match(conn: sqlite3.Connection, tokens: list[str],
                   module_hits: list[str], topk: int) -> list[dict[str, Any]]:
    if not tokens and not module_hits:
        return []
    clauses, params = [], []
    if module_hits:
        clauses.append(f"catalog_lv1 IN ({','.join('?'*len(module_hits))})")
        params.extend(module_hits)
    if tokens:
        like_parts = []
        for t in tokens[:10]:
            like_parts.append("case_name LIKE ? OR catalog_path LIKE ? OR tags LIKE ?")
            kw = f"%{t}%"; params.extend([kw, kw, kw])
        clauses.append(f"({' OR '.join(like_parts)})")
    params.append(topk)
    rows = conn.execute(f"SELECT * FROM cases WHERE {' AND '.join(clauses)} LIMIT ?", params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        h = " ".join([d.get("case_name","") or "", d.get("catalog_path","") or "", d.get("tags","") or ""]).lower()
        d["catalog_score"] = sum(1 for t in tokens if t and t.lower() in h) / max(len(tokens), 1)
        result.append(d)
    return result


def _vector_recall(conn: sqlite3.Connection, query: str, topk: int) -> list[dict[str, Any]]:
    """向量召回。
    项目实测：BM25 + catalog 已足够精准（9063 条用例都是强关键词命名），
    向量层引入噪音、冷启动 ~2-3s、单 query 额外 50-100ms、~300MB RAM。
    关闭，返回空列表。如需启用，import retrieval.vector 后调用 vector_recall。
    """
    return []


def _normalize_bm25_score(raw: float) -> float:
    if raw is None: return 0.0
    return 1.0 / (1.0 + abs(raw) / 5.0)


def _normalize_vector_score(raw: float) -> float:
    """内积已经是归一化后的余弦相似度，范围 [-1, 1] → 映射到 [0, 1]"""
    if raw is None: return 0.0
    return max(0.0, (raw + 1.0) / 2.0)


def _priority_score(priority: str) -> float:
    return RETR_PRIORITY_SCORE.get(priority, 1)


def _merge_three(fts: list[dict], cat: list[dict], vec: list[dict]) -> dict[int, dict]:
    merged: dict[int, dict] = {}
    for item in fts:
        cid = item["case_id"]
        merged[cid] = {**item,
                       "_bm25": _normalize_bm25_score(item.get("fts_score")),
                       "_catalog": 0.0, "_vector": 0.0}
    for item in cat:
        cid = item["case_id"]
        if cid in merged:
            merged[cid]["_catalog"] = item.get("catalog_score", 0)
        else:
            merged[cid] = {**item, "_bm25": 0.0,
                           "_catalog": item.get("catalog_score", 0), "_vector": 0.0}
    for item in vec:
        cid = item["case_id"]
        if cid in merged:
            merged[cid]["_vector"] = _normalize_vector_score(item.get("vector_score", 0))
        else:
            merged[cid] = {**item, "_bm25": 0.0, "_catalog": 0.0,
                           "_vector": _normalize_vector_score(item.get("vector_score", 0))}
    return merged


def rerank(merged: dict[int, dict]) -> list[dict]:
    """
    hybrid 加权重排：
    final = bm25 × w_bm25 + catalog × w_catalog + vector × w_vector + priority × w_pri
    """
    w = RETR_WEIGHTS
    # vector 权重放 config 里，默认 1.2
    w_vec = float(w.get("vector", 1.2))
    scored = []
    for cid, item in merged.items():
        s = (item.get("_bm25", 0) * w.get("bm25", 1.0)
             + item.get("_catalog", 0) * w.get("catalog", 1.5)
             + item.get("_vector", 0) * w_vec
             + _priority_score(item.get("priority", "")) * w.get("priority", 0.3))
        item["final_score"] = round(s, 4)
        if s >= RETR_MIN_SCORE:
            scored.append(item)
    scored.sort(key=lambda x: x["final_score"], reverse=True)
    return scored


def retrieve(conn: sqlite3.Connection, query: str,
             topk_fts: int | None = None,
             topk_catalog: int | None = None,
             topk_final: int | None = None) -> dict[str, Any]:
    topk_fts = topk_fts or RETR_TOPK_FTS
    topk_catalog = topk_catalog or RETR_TOPK_CATALOG
    topk_final = topk_final or 10

    tokens = tokenize(query)
    aliases = load_aliases(conn)
    aliased = expand_with_aliases(tokens, aliases)

    from retrieval.db import get_module_index
    module_idx = get_module_index(conn)
    module_hits = route_catalog(aliased, module_idx)

    fts_query = build_fts_query(aliased)
    fts_rows = _bm25(conn, fts_query, topk_fts)
    cat_rows = _catalog_match(conn, aliased, module_hits, topk_catalog)
    vec_rows = _vector_recall(conn, query, topk_fts)  # 向量也召回 30 条

    merged = _merge_three(fts_rows, cat_rows, vec_rows)
    final = rerank(merged)[:topk_final]

    return {
        "query": query,
        "tokens": tokens,
        "aliased_tokens": aliased,
        "module_hits": module_hits,
        "fts_query": fts_query,
        "fts_count": len(fts_rows),
        "catalog_count": len(cat_rows),
        "vector_count": len(vec_rows),
        "final_count": len(final),
        "final": final,
    }
