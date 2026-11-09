# -*- coding: utf-8 -*-
"""HTTP 适配器：调内部框架的 HTTP API
调用样例等你贴过来后，把 _execute_raw 和 _normalize 里的 TODO 换掉即可。"""
from typing import Any

import requests

from base import ToolBase  # type: ignore
from tool_types import ToolResult, CaseStatus  # type: ignore


class HTTPAdapter(ToolBase):
    """通过 HTTP API 调用执行框架

    配置（运行前填进 config.yaml 或环境变量）：
      base_url: str          # 框架 API 基址，例 "http://test-runner.internal:8080"
      endpoint_run: str      # 执行端点路径，例 "/api/v1/cases/run"
      endpoint_status: str   # 异步查询端点（可选）
      method: str            # POST / GET
      auth_token: str        # 鉴权 token（可选）
      headers: dict          # 额外请求头
    """

    adapter_name = "http"

    def __init__(self, base_url: str = "", endpoint_run: str = "",
                 method: str = "POST", auth_token: str = "",
                 headers: dict | None = None, timeout: int = 300000):
        super().__init__(timeout=timeout)
        self.base_url = base_url.rstrip("/")
        self.endpoint_run = endpoint_run
        self.method = method.upper()
        self.auth_token = auth_token
        self.headers = headers or {}
        if auth_token:
            self.headers["Authorization"] = f"Bearer {auth_token}"

    def _execute_raw(self, case_id: str, params: dict) -> Any:
        # TODO: 等你贴真实调用样例后改这里
        url = f"{self.base_url}{self.endpoint_run}"
        payload = {"case_id": str(case_id), **params}
        try:
            resp = requests.request(
                self.method, url,
                json=payload, headers=self.headers,
                timeout=self.timeout / 1000,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout:
            raise TimeoutError(f"HTTP 执行超时 >{self.timeout}ms")
        except requests.HTTPError as e:
            return {"http_error": e.response.status_code,
                    "body": e.response.text[:500]}

    def _normalize(self, raw: Any) -> ToolResult:
        # TODO: 等你贴真实返回样例后改这里
        if isinstance(raw, dict):
            # 尝试常见字段
            for key in ("status", "result", "pass", "success"):
                if key in raw:
                    val = raw[key]
                    if isinstance(val, str):
                        if val.upper() in ("PASS", "SUCCESS", "OK", "TRUE"):
                            return ToolResult(case_id="", status=CaseStatus.PASS, raw=raw)
                        if val.upper() in ("FAIL", "ERROR", "FALSE"):
                            return ToolResult(case_id="", status=CaseStatus.FAIL, raw=raw,
                                              error=str(raw.get("message", raw.get("error", ""))))
                    elif isinstance(val, bool):
                        return ToolResult(case_id="",
                                          status=CaseStatus.PASS if val else CaseStatus.FAIL,
                                          raw=raw)
            if "http_error" in raw:
                return ToolResult(case_id="", status=CaseStatus.ERROR,
                                  error=f"HTTP {raw['http_error']}", raw=raw)
        return ToolResult(case_id="", status=CaseStatus.PENDING, raw=raw)

    def health_check(self) -> bool:
        if not self.base_url:
            return False
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            return resp.ok
        except Exception:
            # 有些框架没有 /health，换 endpoint_run 做轻量探活
            try:
                requests.request(
                    self.method, f"{self.base_url}{self.endpoint_run}",
                    json={}, headers=self.headers, timeout=5,
                )
                return True
            except Exception:
                return False
