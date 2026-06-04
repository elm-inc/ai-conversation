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
