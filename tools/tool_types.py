# -*- coding: utf-8 -*-
"""统一工具接口规范：数据类定义"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class CaseStatus(str, Enum):
    """框架返回的用例状态（统一枚举，各 adapter 做映射）"""
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"       # 框架异常/崩溃
    PENDING = "PENDING"   # 未执行/跳过
    TIMEOUT = "TIMEOUT"


class StepStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class StepResult:
    """单条测试步骤的结果（框架能返回步骤级时填充）"""
    index: int
    action: str = ""           # 操作描述
    expected: str = ""         # 预期
    observed: str = ""         # 实际观察
    status: StepStatus = StepStatus.PASS
    error: str = ""


@dataclass
class ToolResult:
    """一条用例执行完毕的**统一结果**（不管框架是 CLI/HTTP/SDK）"""
    case_id: str
    status: CaseStatus
    duration_ms: int = 0
    error: str = ""            # 顶层错误（框架崩了/超时）
    steps: list[StepResult] = field(default_factory=list)
    raw: Any = None            # 框架原始返回（调试用，不暴露给用户）
    # 附件路径（框架返回截图/录屏时填）
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ExpectedStep:
    """Agent 生成的预期（出题模式/陪练模式里用户和 Agent 共同产出的）"""
    index: int
    action: str = ""
    expected: str = ""


@dataclass
class ExpectedPlan:
    """Agent 对某用例的**整体执行预期**"""
    case_id: str
    expected_status: CaseStatus = CaseStatus.PASS
    expected_duration_ms: int = 0        # 0 表示不关心
    expected_steps: list[ExpectedStep] = field(default_factory=list)
    key_assertions: list[str] = field(default_factory=list)  # 关键断言关键词


@dataclass
class StepMismatch:
    """步骤级不一致"""
    index: int
    field: str                 # status / expected / observed / duration
    expected: str = ""
    actual: str = ""
    severity: str = "medium"   # high / medium / low


@dataclass
class CompareResult:
    """逐项比对结果"""
    case_id: str
    status_match: bool           # 顶层 PASS/FAIL 是否符合预期
    duration_match: bool = True  # 耗时是否在预期内
    mismatches: list[StepMismatch] = field(default_factory=list)
    consistent_steps: int = 0
    total_steps: int = 0
    # 不一致摘要（给 Agent 转陪练用）
    summary: str = ""
    # 建议下一步（"执行模式" → "陪练模式" 的理由）
    suggest_practice: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
