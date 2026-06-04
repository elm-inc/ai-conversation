# Pipecat Cloud ボットイメージ。
# ★ build context はリポジトリルート (自作 aiconv を含めるため):
#     docker build -f apps/voice-agent/Dockerfile -t <image> .
#
# ベースイメージは bot.py の bot() を実行する Pipecat Cloud ランナー入り。
# 最新タグは Pipecat Cloud のドキュメントで確認すること。
FROM dailyco/pipecat-base:latest

WORKDIR /app

# Pipecat + サービス依存
COPY apps/voice-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 自作ロジック (aiconv: 純 Python・外部依存なし) をインストール
COPY pyproject.toml /tmp/aiconv/pyproject.toml
COPY README.md /tmp/aiconv/README.md
COPY src /tmp/aiconv/src
RUN pip install --no-cache-dir /tmp/aiconv

# ボット本体
COPY apps/voice-agent/bot.py apps/voice-agent/persona.py apps/voice-agent/processors.py ./
