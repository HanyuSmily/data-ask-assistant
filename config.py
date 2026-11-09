# -*- coding: utf-8 -*-
"""配置加载器：读 config.yaml 并暴露 dict。"""
import os

import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# 顶层便捷引用，现有调用可以继续 from config import LLM_BASE_URL 等
_RAW = load_config()
_LLM = _RAW.get("llm", {})

LLM_BASE_URL: str = _LLM.get("base_url", "")
LLM_API_KEY: str = _LLM.get("api_key", "")
LLM_MODEL: str = _LLM.get("model", "")
LLM_TIMEOUT: int = _LLM.get("timeout", 60)

# retrieval 段便捷引用
_RETR = _RAW.get("retrieval", {})
RETR_DB_PATH: str = _RETR.get("db_path", "cases/cases.db")
RETR_TOPK_FTS: int = _RETR.get("topk_fts", 30)
RETR_TOPK_CATALOG: int = _RETR.get("topk_catalog", 15)
RETR_TOPK_FINAL: int = _RETR.get("topk_final", 10)
RETR_STEP_CUTOFF: int = _RETR.get("step_cutoff", 50)
RETR_WEIGHTS: dict = _RETR.get("weights", {"bm25": 1.0, "catalog": 1.5, "priority": 0.3})
RETR_PRIORITY_SCORE: dict = _RETR.get("priority_score", {"P0": 3, "P1": 2, "P2": 1})
RETR_MIN_SCORE: float = _RETR.get("min_score", 0.1)
