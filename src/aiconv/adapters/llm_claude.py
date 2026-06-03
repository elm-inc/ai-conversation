"""Claude (Anthropic) LLMProvider アダプタ。

anthropic SDK の messages.stream を使い、persona/長期記憶ブロックを prompt caching
(cache_control=ephemeral) で固定して TTFT を下げる (設計 §[3])。

SDK は遅延 import — コア本体は `--extra providers` 無しでも import できる。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any, cast

from ..core.events import TokenChunk
from ..core.ports import Capability


class ClaudeLLM:
    capabilities = Capability(
        streaming_partials=True,
        languages=("ja", "en"),
        notes="anthropic messages.stream; persona/記憶を prompt caching で固定",
    )

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 1024,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        system: str = "",
        cache_hint: bool = True,
    ) -> AsyncIterator[TokenChunk]:
        client = self._ensure_client()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": cast("Any", messages),
        }
        if system:
            block: dict[str, Any] = {"type": "text", "text": system}
            if cache_hint:
                # persona/記憶ブロックをキャッシュし TTFT を下げる
                block["cache_control"] = {"type": "ephemeral"}
            kwargs["system"] = [block]

        index = 0
        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield TokenChunk(text=text, index=index, is_first=(index == 0))
                index += 1
