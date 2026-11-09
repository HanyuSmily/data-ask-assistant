# -*- coding: utf-8 -*-
"""ToolBase：执行框架的统一抽象基类
所有 adapter（CLI / HTTP / SDK）继承这个类，Agent 只依赖接口不依赖实现。"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from tool_types import (  # type: ignore  # sys.path 已注入 tools/
    ToolResult, CaseStatus, StepResult, StepStatus,
    ExpectedPlan, CompareResult, StepMismatch,
)


class ToolBase(ABC):
    """统一接口：execute / health_check / normalize_status

    子类必须实现:
      - _execute_raw(case_id, params) -> Any   # 框架原始返回
      - _normalize(raw) -> ToolResult           # 把原始返回转成统一结构
      - health_check() -> bool                  # 框架是否可用
    """

    adapter_name: str = "base"  # CLIAdapter / HTTPAdapter / SDKAdapter

    def __init__(self, timeout: int = 300000):
        self.timeout = timeout  # 毫秒，单条用例最长执行时间

    # ---------- 外部调用入口（模板方法）----------

    def execute(self, case_id: str, params: dict | None = None) -> ToolResult:
        """执行一条用例，返回统一结果；子类不需要覆盖这个方法"""
        t0 = time.time()
        try:
            raw = self._execute_raw(case_id, params or {})
            result = self._normalize(raw)
            result.duration_ms = int((time.time() - t0) * 1000)
            # 确保 case_id 对齐（normalize 可能没填）
            result.case_id = str(case_id)
        except TimeoutError:
            result = ToolResult(
                case_id=str(case_id),
                status=CaseStatus.TIMEOUT,
                error=f"执行超时（>{self.timeout}ms）",
            )
        except Exception as e:
            result = ToolResult(
                case_id=str(case_id),
                status=CaseStatus.ERROR,
                error=f"{type(e).__name__}: {e}",
            )
        return result

    @abstractmethod
    def _execute_raw(self, case_id: str, params: dict) -> Any:
        """子类实现：调框架拿原始返回"""
        ...

    @abstractmethod
    def _normalize(self, raw: Any) -> ToolResult:
        """子类实现：把原始返回转成 ToolResult"""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """框架是否可用（连通性 + 依赖就绪）"""
        ...

    # ---------- 比对（统一实现，所有 adapter 共用）----------

    def compare(self, expected: ExpectedPlan, actual: ToolResult) -> CompareResult:
        """Agent 预期 vs 框架实际，逐项比对"""
        mismatches: list[StepMismatch] = []

        # 1) 顶层状态
        status_match = (expected.expected_status == actual.status)
        if not status_match:
            mismatches.append(StepMismatch(
                index=0, field="status",
                expected=expected.expected_status.value,
                actual=actual.status.value,
                severity="high",
            ))

        # 2) 耗时（预期 > 0 时才比）
        duration_match = True
        if expected.expected_duration_ms > 0 and actual.duration_ms > 0:
            # 允许 ±50% 偏差
            ratio = actual.duration_ms / expected.expected_duration_ms
            if ratio > 1.5 or ratio < 0.5:
                duration_match = False
                mismatches.append(StepMismatch(
                    index=0, field="duration_ms",
                    expected=str(expected.expected_duration_ms),
                    actual=str(actual.duration_ms),
                    severity="low",
                ))

        # 3) 步骤级比对（两边都有步骤时才比）
        exp_steps = expected.expected_steps
        act_steps = actual.steps
        consistent = 0
        for i, exp in enumerate(exp_steps):
            if i >= len(act_steps):
                mismatches.append(StepMismatch(
                    index=i + 1, field="status",
                    expected="(有预期)", actual="(框架未返回此步骤)",
                    severity="medium",
                ))
                continue
            act = act_steps[i]
            if act.status == StepStatus.PASS and exp.expected:
                # 用简单的关键词重合度做比对（不做 NLP，够用）
                exp_words = set(exp.expected.replace("；", " ").replace("，", " ").split())
                obs_words = set(act.observed.replace("；", " ").replace("，", " ").split())
                overlap = exp_words & obs_words
                if not overlap and act.status == StepStatus.PASS:
                    # 框架说 PASS 但观察和预期完全没交集 → 可疑
                    mismatches.append(StepMismatch(
                        index=i + 1, field="observed",
                        expected=exp.expected[:100],
                        actual=act.observed[:100],
                        severity="medium",
                    ))
                else:
                    consistent += 1
            elif act.status != StepStatus.PASS:
                mismatches.append(StepMismatch(
                    index=i + 1, field="status",
                    expected="PASS", actual=act.status.value,
                    severity="high" if act.status == StepStatus.FAIL else "medium",
                ))
            else:
                consistent += 1

        # 4) 关键断言（expected.key_assertions 是关键词，在 actual.error 或 act.observed 里搜）
        for kw in expected.key_assertions:
            if kw:
                found = kw in (actual.error or "") or any(
                    kw in (s.observed or s.error or "") for s in act_steps
                )
                if not found:
                    mismatches.append(StepMismatch(
                        index=0, field="assertion",
                        expected=kw, actual="(未在输出中出现)",
                        severity="medium",
                    ))

        summary = self._build_summary(status_match, duration_match, mismatches)
        suggest_practice = not status_match or any(m.severity == "high" for m in mismatches)

        return CompareResult(
            case_id=actual.case_id,
            status_match=status_match,
            duration_match=duration_match,
            mismatches=mismatches,
            consistent_steps=consistent,
            total_steps=max(len(exp_steps), len(act_steps)),
            summary=summary,
            suggest_practice=suggest_practice,
        )

    @staticmethod
    def _build_summary(status_match, duration_match, mismatches) -> str:
        if status_match and duration_match and not mismatches:
            return "执行结果与预期完全一致 ✅"
        parts = []
        if not status_match:
            high = [m for m in mismatches if m.severity == "high" and m.field == "status"]
            if high:
                parts.append(f"❌ 顶层状态不符：预期 {high[0].expected}，实际 {high[0].actual}")
        if not duration_match:
            parts.append("⏱️ 耗时偏差较大")
        step_mismatches = [m for m in mismatches if m.index > 0]
        if step_mismatches:
            parts.append(f"有 {len(step_mismatches)} 处步骤不一致")
        assertion_misses = [m for m in mismatches if m.field == "assertion"]
        if assertion_misses:
            parts.append(f"有 {len(assertion_misses)} 条关键断言未命中")
        return "；".join(parts) if parts else "部分字段存在偏差"
