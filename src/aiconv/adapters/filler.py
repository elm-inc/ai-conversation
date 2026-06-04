"""相槌/フィラーの音声供給アダプタ (レイテンシ隠蔽)。

- MockFiller: 無音フレームを一定数返す (オフライン PoC / テスト用)。
- PrerenderedFiller: 事前録音した声優フィラー WAV をループ再生する。
  クリップは scripts/render_fillers.py で ElevenLabs から一度だけ合成しておく。
"""

from __future__ import annotations

import asyncio
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from ..core.events import AudioFormat, AudioFrame

_PCM_16K = AudioFormat(sample_rate=16_000, channels=1, sample_width=2)


class MockFiller:
    """無音フレームを最大 frames 個 yield する (本応答が来れば呼び出し側が打ち切る)。

    実音声と同様にフレーム長ぶん間隔を空けて流す (gap_ms)。これが無いと無限に速く
    回って本応答到着前に大量再生してしまう。
    """

    def __init__(
        self, *, frames: int = 50, samples_per_frame: int = 160, gap_ms: float = 10.0
    ) -> None:
        self._n = frames
        self._silence = b"\x00\x00" * samples_per_frame
        self._gap_ms = gap_ms

    async def filler(self) -> AsyncIterator[AudioFrame]:
        for seq in range(self._n):
            yield AudioFrame(data=self._silence, ts_ms=0.0, seq=seq, fmt=_PCM_16K)
            if self._gap_ms:
                await asyncio.sleep(self._gap_ms / 1000.0)


class PrerenderedFiller:
    """事前録音した声優フィラー WAV をフレーム化してループ供給する。

    `clips` 内の複数クリップを turn ごとに round-robin で選ぶ (毎回同じ相槌を避ける)。
    本応答が来るまで流せるよう、選んだクリップを繰り返し yield する。
    """

    def __init__(self, clips_dir: str, *, frame_ms: int = 20, max_loops: int = 4) -> None:
        self.frame_ms = frame_ms
        self.max_loops = max_loops
        self._clips = sorted(str(p) for p in Path(clips_dir).glob("*.wav"))
        self._idx = 0

    @property
    def available(self) -> bool:
        return bool(self._clips)

    def _read_frames(self, path: str) -> list[AudioFrame]:
        with wave.open(path, "rb") as w:
            fmt = AudioFormat(
                sample_rate=w.getframerate(),
                channels=w.getnchannels(),
                sample_width=w.getsampwidth(),
            )
            spf = int(fmt.sample_rate * self.frame_ms / 1000)
            out: list[AudioFrame] = []
            seq = 0
            while True:
                data = w.readframes(spf)
                if not data:
                    break
                out.append(AudioFrame(data=data, ts_ms=seq * self.frame_ms, seq=seq, fmt=fmt))
                seq += 1
            return out

    async def filler(self) -> AsyncIterator[AudioFrame]:
        if not self._clips:
            return
        path = self._clips[self._idx % len(self._clips)]
        self._idx += 1
        frames = self._read_frames(path)
        for _ in range(self.max_loops):
            for fr in frames:
                yield fr
