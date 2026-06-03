"""Claude (Anthropic) LLMProvider アダプタ — スケルトン。

Phase 0 次段で anthropic SDK の streaming + prompt caching を実装する。
persona/長期記憶ブロックを cache_control で固定し TTFT を下げる方針 (設計 §[3])。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from ..core.events import TokenChunk
from ..core.ports import Capability


class ClaudeLLM:
    capabilities = Capability(
        streaming_partials=True,
        languages=("ja", "en"),
        notes="anthropic; prompt caching でpersona/記憶を固定予定",
    )

    def __init__(self, *, model: str = "claude-sonnet-4-6", api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        system: str = "",
        cache_hint: bool = True,
    ) -> AsyncIterator[TokenChunk]:
        raise NotImplementedError(
            "Phase 0 スケルトン: anthropic streaming + prompt caching を実装する (AIC-1 次段)"
        )
        yield  # Protocol を async-generator にするため (raise 後で到達しない)
