# -*- coding: utf-8 -*-
"""Embedding + FAISS 向量检索层。

用 sentence-transformers 把用例 title + steps + expected 做 embedding，
存 FAISS IndexFlatIP（内积=余弦相似度），跟 BM25 做 hybrid 融合。
"""
from __future__ import annotations

import os
import sqlite3
import numpy as np
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = PROJECT_ROOT / "cases" / "faiss_index.bin"
IDMAP_PATH = PROJECT_ROOT / "cases" / "faiss_ids.npy"
EMBEDDING_PATH = PROJECT_ROOT / "cases" / "embeddings.npy"

# 模型：轻量、中文友好、多语言都能兜住
# all-MiniLM-L6-v2 是英文，换 paraphrase-multilingual-MiniLM-L12-v2 支持中文
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


class VectorIndex:
    """FAISS 向量索引管理器。懒加载，首次 build 后持久化到磁盘。"""

    def __init__(self, db_path: str | os.PathLike, model_name: str = MODEL_NAME):
        self.db_path = str(db_path)
        self.model_name = model_name
        self._model = None
        self._index = None
        self._ids: np.ndarray | None = None  # case_id 数组，行号对应 index
        self._dim: int | None = None

    # ---- 懒加载模型 ----
    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            print(f"[VectorIndex] loading model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
            self._dim = self._model.get_sentence_embedding_dimension()
            print(f"[VectorIndex] dim={self._dim}")
        return self._model

    # ---- 构建（首次 / 增量）----
    def build(self, rebuild: bool = False) -> int:
        """从 SQLite 读全量 cases，embedding + 建 FAISS。"""
        import faiss
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        existing_ids = set()
        if not rebuild and INDEX_PATH.exists():
            self._ids = np.load(str(IDMAP_PATH))
            existing_ids = set(int(x) for x in self._ids)

        rows = conn.execute(
            "SELECT case_id, case_name, precondition, steps, expected, tags, catalog_path FROM cases"
        ).fetchall()
        conn.close()

        # 过滤需要 embedding 的（增量）
        new_rows = []
        for r in rows:
            if rebuild or r["case_id"] not in existing_ids:
                new_rows.append(r)

        print(f"[VectorIndex] 需 embedding: {len(new_rows)} 条 (rebuild={rebuild})")

        if new_rows:
            # 拼接文本做 embedding
            texts = [
                self._compose_text(r) for r in new_rows
            ]
            emb = self.model.encode(texts, batch_size=64, show_progress_bar=True,
                                     normalize_embeddings=True)
            new_ids = np.array([r["case_id"] for r in new_rows], dtype=np.int64)

            if rebuild or not INDEX_PATH.exists():
                # 全新构建
                dim = emb.shape[1]
                self._dim = dim
                self._index = faiss.IndexFlatIP(dim)
                self._index.add(emb.astype(np.float32))
                self._ids = new_ids
            else:
                # 增量：load 旧 + concat
                old_emb = np.load(str(EMBEDDING_PATH))
                old_ids = self._ids
                all_emb = np.vstack([old_emb, emb.astype(np.float32)])
                all_ids = np.concatenate([old_ids, new_ids])
                dim = all_emb.shape[1]
                self._dim = dim
                self._index = faiss.IndexFlatIP(dim)
                self._index.add(all_emb.astype(np.float32))
                self._ids = all_ids

            # 持久化
            faiss.write_index(self._index, str(INDEX_PATH))
            np.save(str(IDMAP_PATH), self._ids)
            np.save(str(EMBEDDING_PATH), all_emb if not rebuild else emb.astype(np.float32))
            print(f"[VectorIndex] 索引已保存: {INDEX_PATH}, {len(self._ids)} 条")

        # 即使没有增量，也要确保 self._index / self._ids 就绪
        if self._index is None and INDEX_PATH.exists():
            import faiss
            self._index = faiss.read_index(str(INDEX_PATH))
            self._ids = np.load(str(IDMAP_PATH))
            self._dim = self._index.d
            print(f"[VectorIndex] 已加载已有索引: {len(self._ids)} 条")

        return len(self._ids) if self._ids is not None else 0

    @staticmethod
    def _compose_text(r: sqlite3.Row | dict) -> str:
        """把一条用例的几个字段拼成 embedding 输入文本。"""
        name = r["case_name"] or ""
        pre = r["precondition"] or ""
        step = r["steps"] or ""
        exp = r["expected"] or ""
        cat = r["catalog_path"] or ""
        return f"{name} | 步骤: {step[:200]} | 预期: {exp[:150]} | 分类: {cat}"

    # ---- 加载已有索引 ----
    def load(self) -> bool:
        if INDEX_PATH.exists() and IDMAP_PATH.exists():
            import faiss
            self._index = faiss.read_index(str(INDEX_PATH))
            self._ids = np.load(str(IDMAP_PATH))
            self._dim = self._index.d
            return True
        return False

    def is_ready(self) -> bool:
        return self._index is not None or (INDEX_PATH.exists() and self.load())

    # ---- 查询 ----
    def search(self, query: str, topk: int = 30) -> list[dict[str, Any]]:
        """向量检索 TopK。返回 [{case_id, vector_score}, ...]"""
        if not self.is_ready():
            return []
        import faiss
        q = self.model.encode([query], normalize_embeddings=True).astype(np.float32)
        scores, idx = self._index.search(q, min(topk, self._index.ntotal))
        results = []
        for i in range(idx.shape[1]):
            if idx[0][i] < 0:
                break
            cid = int(self._ids[idx[0][i]])
            results.append({"case_id": cid, "vector_score": float(scores[0][i])})
        return results


# ---- 全局单例 ----
_vector_index: VectorIndex | None = None


def get_vector_index(db_path: str | os.PathLike | None = None) -> VectorIndex:
    global _vector_index
    if _vector_index is None:
        if db_path is None:
            from config import RETR_DB_PATH
            db_path = RETR_DB_PATH
        _vector_index = VectorIndex(db_path)
    return _vector_index


def vector_recall(conn: sqlite3.Connection, query: str, topk: int = 30) -> list[dict[str, Any]]:
    """向量召回 → join cases 表拿完整字段 → 返回列表（带 vector_score）"""
    vi = get_vector_index()
    if not vi.is_ready():
        return []
    hits = vi.search(query, topk)
    if not hits:
        return []
    ids = [h["case_id"] for h in hits]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM cases WHERE case_id IN ({placeholders})", ids
    ).fetchall()
    # 把 vector_score 回填
    score_map = {h["case_id"]: h["vector_score"] for h in hits}
    result = []
    for r in rows:
        d = dict(r)
        d["vector_score"] = score_map.get(d["case_id"], 0.0)
        result.append(d)
    return result
