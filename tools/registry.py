# -*- coding: utf-8 -*-
"""工具注册表：多 adapter 并存 + 白名单 + 确认门 + case_mapping"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import yaml

from cli_adapter import CLIAdapter, IPCPanelCheckAnchors, IPCPanelRunCase  # type: ignore

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
_MAPPING_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cases", "case_mapping.json")


@dataclass
class Whitelist:
    allowed_case_ids: set[str] = field(default_factory=set)
    allowed_catalog_tags: set[str] = field(default_factory=set)
    forbidden_case_ids: set[str] = field(default_factory=set)
    forbidden_priorities: set[str] = field(default_factory=set)

    def can_run(self, case_id: str, priority: str = "", catalog_tag: str = "") -> tuple[bool, str]:
        cid = str(case_id)
        if cid in self.forbidden_case_ids:
            return False, f"用例 {cid} 在黑名单中"
        if priority and priority in self.forbidden_priorities:
            return False, f"优先级 {priority} 禁止自动执行"
        if cid in self.allowed_case_ids:
            return True, "白名单"
        if catalog_tag and catalog_tag in self.allowed_catalog_tags:
            return True, f"分类白名单: {catalog_tag}"
        return True, "默认（需执行确认门）"


@dataclass
class CaseMapping:
    """case_mapping.json 加载器：tms_case_id → skill cases/*.json 路径
    所有映射必须人工确认后才生效（LLM 只生成草稿，不自动写入）"""
    _mapping: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "CaseMapping":
        m = cls()
        if os.path.exists(_MAPPING_PATH):
            with open(_MAPPING_PATH, encoding="utf-8") as f:
                m._mapping = json.load(f)
        return m

    def get_path(self, tms_case_id: str) -> str | None:
        return self._mapping.get(str(tms_case_id))

    def save(self):
        os.makedirs(os.path.dirname(_MAPPING_PATH), exist_ok=True)
        with open(_MAPPING_PATH, "w", encoding="utf-8") as f:
            json.dump(self._mapping, f, ensure_ascii=False, indent=2)

    def all_ids(self) -> list[str]:
        return list(self._mapping.keys())


class ToolRegistry:
    """全局单例：管理 check_anchors + run_case 两个 adapter
    以及白名单、确认门、case_mapping。"""

    _instance: "ToolRegistry | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.check_anchors: IPCPanelCheckAnchors | None = None
        self.run_case: IPCPanelRunCase | None = None
        self.whitelist = Whitelist()
        self.mapping = CaseMapping.load()
        self._pending_confirmations: dict[str, dict] = {}
        self._load_config()

    def _load_config(self):
        if not os.path.exists(_CONFIG_PATH):
            return
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        tool_cfg = cfg.get("tool", {})
        if not tool_cfg:
            return
        skill_root = tool_cfg.get("skill_root", "")

        # 两个 adapter 都初始化（无 skill_root 时 cwd=None 会 fallback 到相对路径）
        self.check_anchors = IPCPanelCheckAnchors(
            skill_root=skill_root,
            timeout=tool_cfg.get("check_anchors_timeout", 60000),
        )
        self.run_case = IPCPanelRunCase(
            skill_root=skill_root,
            timeout=tool_cfg.get("run_case_timeout", 600000),
        )

        wl_cfg = tool_cfg.get("whitelist", {})
        self.whitelist = Whitelist(
            allowed_case_ids=set(str(x) for x in wl_cfg.get("allowed_case_ids", [])),
            allowed_catalog_tags=set(wl_cfg.get("allowed_catalog_tags", [])),
            forbidden_case_ids=set(str(x) for x in wl_cfg.get("forbidden_case_ids", [])),
            forbidden_priorities=set(wl_cfg.get("forbidden_priorities", [])),
        )

    # ---------- health_check（组合判断）----------

    def health_check(self) -> dict:
        """离线门禁 vs 真机分别检查，返回分项 bool + 汇总"""
        out = {
            "check_anchors": self.check_anchors.health_check() if self.check_anchors else False,
            "run_case": self.run_case.health_check() if self.run_case else False,
            "mapping_ready": len(self.mapping._mapping) > 0,
        }
        out["all_ok"] = all(out.values())
        return out

    # ---------- 执行确认门 ----------

    def request_confirmation(self, case_id: str, context: dict) -> str:
        cid = str(case_id)
        self._pending_confirmations[cid] = context
        return (
            f"🔒 **执行确认门**\n\n"
            f"准备执行用例 `{cid}`。\n"
            f"原因：{context.get('reason', 'Agent 请求执行')}\n\n"
            f"在 UI 上点「确认执行」继续，或告诉我「取消」。"
        )

    def confirm_and_check_anchors(self) -> CLIAdapter | None:
        """用户确认后 → 跑离线门禁（无真机）"""
        if self.check_anchors is None:
            return None
        return self.check_anchors.execute("(离线门禁)")

    def confirm_and_run_case(self, case_id: str, case_path: str) -> CLIAdapter | None:
        """用户确认后 → 真机执行某条用例
        case_path 来自 case_mapping（agent 查 mapping.get_path() 拿到）"""
        if self.run_case is None:
            return None
        return self.run_case.execute(
            case_id,
            params={"case_path": case_path},
        )

    def cancel_confirmation(self, case_id: str):
        self._pending_confirmations.pop(str(case_id), None)

    def pending_confirm_count(self) -> int:
        return len(self._pending_confirmations)


def get_registry() -> ToolRegistry:
    return ToolRegistry()
