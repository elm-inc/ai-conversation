"""エンジンレジストリ。

このパッケージの import は軽量に保つ (重い依存は各エンジンの check()/prepare() 以降)。
エンジン追加時は ENGINE_NAMES と create_engine に 1 ブロック足す。
"""

from __future__ import annotations

from .base import Engine, EngineUnavailableError, SynthResult

ENGINE_NAMES: tuple[str, ...] = ("elevenlabs", "espnet", "voicevox", "kokoro")


def create_engine(name: str) -> Engine:
    """名前からエンジンを生成する。エンジンモジュールはここで遅延 import する。"""
    if name == "elevenlabs":
        from .elevenlabs import ElevenLabsEngine

        return ElevenLabsEngine()
    if name == "espnet":
        from .espnet import EspnetEngine

        return EspnetEngine()
    if name == "voicevox":
        from .voicevox import VoicevoxEngine

        return VoicevoxEngine()
    if name == "kokoro":
        from .kokoro import KokoroEngine

        return KokoroEngine()
    raise ValueError(f"未知のエンジン: {name} (候補: {', '.join(ENGINE_NAMES)})")


__all__ = [
    "ENGINE_NAMES",
    "Engine",
    "EngineUnavailableError",
    "SynthResult",
    "create_engine",
]
