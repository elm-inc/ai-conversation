# Pipecat Cloud ボットイメージ (Pipecat CLI canonical テンプレート準拠)。
# build context は apps/voice-agent。base image が /app/bot.py の bot() を実行する。
FROM dailyco/pipecat-base:latest

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# 依存をロックファイルから解決
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# ボット本体
COPY ./bot.py bot.py
# TTS_ENGINE=aivis 用アダプタ (既定の ElevenLabs 経路では未使用)。
# NOTE: aiconv (L0 正規化 / VoicevoxClient) は未同梱 — cloud で aivis を使うには同梱が
# 別途必要 (processors.py の staging 注記と同じ)。無くても factory が ElevenLabs に倒す。
COPY ./aivis_tts.py aivis_tts.py
