"""セルフホスト TTS エンジンの低レベル呼び出し層 (同期プリミティブ)。

core の TTSProvider アダプタ (tts_espnet / tts_aivis) と P0 選定ハーネス
(apps/tts-bench/engines/) が**同じエンジン呼び出しを共有**するための置き場 (二重実装の禁止)。
async 化・AudioFrame 正規化・L0/L1 フロントエンドはアダプタ側、計測はハーネス側の責務で、
ここは「text → 16-bit mono PCM + sample_rate」の生合成だけを提供する。

規約 (apps/tts-bench/engines/base.py と同じ graceful degradation):
- import は軽量 (espnet2 / torch / ネットワークに触らない)。
- 重いロード・接続は呼び出し時まで遅延し、利用不可は EngineUnavailableError
  (導入・起動案内をメッセージに含む) で明確に伝える。
"""

from __future__ import annotations


class EngineUnavailableError(RuntimeError):
    """エンジン利用不可 (ライブラリ未導入 / サーバ未起動)。メッセージが導入・起動案内を兼ねる。

    apps/tts-bench の engines.base.EngineUnavailableError とは別クラス (ハーネス側が
    自分の境界で翻訳する)。core アダプタはこの例外をそのまま呼び出し元へ伝える。
    """
