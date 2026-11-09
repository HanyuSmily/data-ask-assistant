# -*- coding: utf-8 -*-
"""LLM 调用封装层：OpenAI SDK 兼容任意 OpenAI-compatible 端点。
换平台/换模型只改 config.yaml，不改这里、不改 chat_handler。"""
from __future__ import annotations

from typing import Iterable

from openai import OpenAI, APIError, RateLimitError, APITimeoutError


class LLMClient:
    """薄封装：构造时注入 base_url / api_key / model，调用时只管传 messages"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        self.model = model
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
        self._timeout = timeout

    def chat(self, system_prompt: str, user_text: str, history: Iterable | None = None) -> str:
        """单轮/带历史的 chat completions"""
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            for role, content in history:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text})
        return self.complete(messages)

    def complete(self, messages: list[dict]) -> str:
        """底层调用：直接传 messages 列表"""
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return resp.choices[0].message.content or ""

    def health_check(self) -> bool:
        """快速连通性探测（拿一个轻量回复即成功）"""
        try:
            self.complete([
                {"role": "system", "content": "只回复 OK"},
                {"role": "user", "content": "ping"},
            ])
            return True
        except Exception:
            return False


# ---------- 错误分类（让上层 chat_handler 能给用户可读的兜底消息） ----------

LLM_EXCEPTIONS = (APIError, RateLimitError, APITimeoutError)


def classify_error(exc: Exception) -> str:
    """把 SDK 异常翻译成面向用户的一句话"""
    if isinstance(exc, APITimeoutError):
        return f"[LLM 超时] 请求在 {getattr(exc, 'timeout', '未知')}s 内未完成"
    if isinstance(exc, RateLimitError):
        return "[LLM 限流] 服务端返回 429，请稍后重试"
    if isinstance(exc, APIError):
        return f"[LLM {getattr(exc, 'status_code', 'HTTP')}] {str(exc)[:200]}"
    return f"[LLM 调用异常] {type(exc).__name__}: {exc}"
