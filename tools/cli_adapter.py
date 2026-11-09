# -*- coding: utf-8 -*-
"""CLI 适配器：通用 subprocess 包装
两个子类（check_anchors / run_case）各自实现 normalize。
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from base import ToolBase  # type: ignore
from tool_types import ToolResult, CaseStatus, StepResult, StepStatus  # type: ignore


class CLIAdapter(ToolBase):
    """通用 CLI：command + args_template 带占位符（{case_id} / {case_path} / {params:json}）

    子类可以覆盖 _normalize；基类默认用 exit_code 判 PASS/FAIL。
    """

    adapter_name = "cli"

    def __init__(self, command: str = "", args_template: list[str] | None = None,
                 timeout: int = 300000, cwd: str | None = None):
        super().__init__(timeout=timeout)
        self.command = command or "echo"
        self.args_template = args_template or ["run", "{case_id}"]
        self.cwd = cwd  # 子进程工作目录（skill 脚本需要在自己目录下跑）

    def _execute_raw(self, case_id: str, params: dict) -> Any:
        args = []
        for a in self.args_template:
            if a == "{case_path}":
                args.append(params.get("case_path", str(case_id)))
            elif a == "{params:json}":
                args.append(json.dumps(params, ensure_ascii=False))
            else:
                args.append(a.replace("{case_id}", str(case_id)))
        cmd = [self.command] + args
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self.timeout / 1000,
                cwd=self.cwd,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
            return {
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "json": parsed,
            }
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"CLI 执行超时 >{self.timeout}ms")

    def _normalize(self, raw: Any) -> ToolResult:
        """基类默认：exit_code=0 → PASS，其余 → FAIL"""
        if isinstance(raw, dict):
            ec = raw.get("exit_code", 0)
            if ec == 0:
                status = CaseStatus.PASS
            else:
                status = CaseStatus.FAIL
                # 把 stderr 的前 300 字塞进 error
                stderr = raw.get("stderr", "") or ""
                status = CaseStatus.FAIL
                return ToolResult(
                    case_id="",
                    status=status,
                    error=stderr[:300],
                    raw=raw,
                )
        else:
            status = CaseStatus.PENDING
        return ToolResult(case_id="", status=status, raw=raw)

    def health_check(self) -> bool:
        try:
            subprocess.run(
                [self.command, "--help"],
                capture_output=True, text=True, timeout=5,
            )
            return True
        except Exception:
            return False


# ============================================================
# 两个专门子类：离线门禁 / 真机端到端
# ============================================================

class IPCPanelCheckAnchors(CLIAdapter):
    """ipc-panel-case-builder 离线门禁体检
    入口：scripts/check_anchors.py
    退出码 0 = 硬错误 0；非 0 = 有门禁错误。
    无真机依赖（除了脚本可能会访问 anchors 目录）。
    """

    adapter_name = "ipc_check_anchors"

    def __init__(self, skill_root: str = "", timeout: int = 60000):
        check_script = f"{skill_root}/scripts/check_anchors.py" if skill_root else "check_anchors.py"
        super().__init__(
            command="python",
            args_template=[check_script],
            timeout=timeout,
            cwd=skill_root or None,
        )

    def _normalize(self, raw: Any) -> ToolResult:
        """exit_code=0 → PASS；非 0 → FAIL，stderr 里通常有关键词"""
        ec = raw.get("exit_code", 0) if isinstance(raw, dict) else 0
        status = CaseStatus.PASS if ec == 0 else CaseStatus.FAIL
        stderr = raw.get("stderr", "") or ""
        stdout = raw.get("stdout", "") or ""
        error_text = stderr if stderr else stdout[:500]
        return ToolResult(
            case_id="(离线门禁)",
            status=status,
            error="" if status == CaseStatus.PASS else error_text[:500],
            raw=raw,
        )

    def health_check(self) -> bool:
        """离线门禁：脚本能跑通（跑一次会修改 ANCHOR_COVERAGE.md，
        所以 health_check 改用 --dry-run 的 help；不行就直接 try 跑一次
        看 exit_code 不崩即可）"""
        try:
            # 直接跑一次，不管 PASS/FAIL，只要 exit_code 不是 127（command not found）
            result = subprocess.run(
                [self.command] + self.args_template,
                capture_output=True, text=True,
                timeout=self.timeout / 1000,
                cwd=self.cwd,
            )
            return result.returncode != 127
        except Exception:
            return False


class IPCPanelRunCase(CLIAdapter):
    """ipc-panel-case-builder 真机端到端执行
    入口：lib/runner.py <case_path>
    case_path 是 skill 自己 cases/ 下的 json 路径（不是数字 case_id）。
    结果解析等用户贴 stdout 样例后再补 normalize。
    """

    adapter_name = "ipc_run_case"

    def __init__(self, skill_root: str = "", timeout: int = 600000):
        runner = f"{skill_root}/lib/runner.py" if skill_root else "../lib/runner.py"
        # case_path 占位在 params 里传
        super().__init__(
            command="python",
            args_template=[runner, "{case_path}"],
            timeout=timeout,
            cwd=skill_root or None,
        )

    def _normalize(self, raw: Any) -> ToolResult:
        """等用户贴 runner.py stdout 样例后替换。
        当前先用 exit_code 兜底，stdout/stderr 塞进 raw。"""
        ec = raw.get("exit_code", 0) if isinstance(raw, dict) else 0
        stdout = raw.get("stdout", "") or ""
        stderr = raw.get("stderr", "") or ""
        if ec == 0:
            status = CaseStatus.PASS
        else:
            # 关键词识别（用户贴样例后细化）
            lower = (stdout + "\n" + stderr).lower()
            if "timeout" in lower:
                status = CaseStatus.TIMEOUT
            elif "error" in lower or "exception" in lower or ec != 0:
                status = CaseStatus.FAIL
            else:
                status = CaseStatus.FAIL
        return ToolResult(
            case_id="",  # execute() 外层填
            status=status,
            error=(stderr or stdout[:300]) if status != CaseStatus.PASS else "",
            raw=raw,
        )

    def health_check(self) -> bool:
        """真机版：adb devices 至少有一台设备 + runner.py 能被 python 找到"""
        # 1) adb devices
        try:
            adb = subprocess.run(
                ["adb", "devices"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l for l in adb.stdout.splitlines() if l.strip() and "List of devices" not in l]
            if not lines:
                return False
        except Exception:
            return False
        # 2) runner.py 能 parse（--help 或直接 import）
        try:
            subprocess.run(
                [self.command, self.args_template[0], "--help"],
                capture_output=True, text=True, timeout=5,
                cwd=self.cwd,
            )
            return True
        except Exception:
            # 没 --help 就算了，脚本能被 import 就行
            try:
                subprocess.run(
                    [self.command, "-c", f"import importlib.util,sys; spec=importlib.util.spec_from_file_location('runner','{self.args_template[0]}'); sys.exit(0 if spec else 1)"],
                    capture_output=True, text=True, timeout=5,
                    cwd=self.cwd,
                )
                return True
            except Exception:
                return False
