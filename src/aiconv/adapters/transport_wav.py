"""ローカル WAV ファイルトランスポート (stdlib のみ)。

WebRTC を立てずに、実 STT/LLM/TTS を通したベースライン遅延を計測するための足場。
入力 WAV を inbound フレームとして流し、再生フレームを出力 WAV に書き出す。

Pipecat による本番 WebRTC トランスポートは別アダプタ (transport_pipecat) で、
クライアント front-end が出来てから差し替える。
"""

from __future__ import annotations

import asyncio
import wave
from collections.abc import AsyncIterator

from ..core.events import AudioFormat, AudioFrame


class WavFileTransport:
    def __init__(
        self,
        in_path: str,
        out_path: str | None = None,
        *,
        frame_ms: int = 20,
        realtime: bool = True,
    ) -> None:
        self.in_path = in_path
        self.out_path = out_path
        self.frame_ms = frame_ms
        # ライブのマイク入力を模擬し、フレームを実時間ペースで流す。
        # ストリーミング STT は実時間到来を前提に終端判定するため、これが無いと
        # 先頭欠落・終端誤判定を起こす。遅延計測も実時間で意味を持つ。
        self.realtime = realtime
        self.played: list[AudioFrame] = []
        self._out_fmt: AudioFormat | None = None

    async def inbound(self) -> AsyncIterator[AudioFrame]:
        with wave.open(self.in_path, "rb") as w:
            fmt = AudioFormat(
                sample_rate=w.getframerate(),
                channels=w.getnchannels(),
                sample_width=w.getsampwidth(),
            )
            samples_per_frame = int(fmt.sample_rate * self.frame_ms / 1000)
            seq = 0
            while True:
                data = w.readframes(samples_per_frame)
                if not data:
                    break
                yield AudioFrame(data=data, ts_ms=seq * self.frame_ms, seq=seq, fmt=fmt)
                seq += 1
                if self.realtime:
                    await asyncio.sleep(self.frame_ms / 1000.0)

    async def play(self, frame: AudioFrame) -> None:
        self._out_fmt = self._out_fmt or frame.fmt
        self.played.append(frame)

    async def stop_playback(self) -> None:
        self.write_output()

    def write_output(self) -> None:
        """蓄積した再生フレームを出力 WAV に書き出す。"""
        if not self.out_path or not self.played or self._out_fmt is None:
            return
        with wave.open(self.out_path, "wb") as w:
            w.setnchannels(self._out_fmt.channels)
            w.setsampwidth(self._out_fmt.sample_width)
            w.setframerate(self._out_fmt.sample_rate)
            for frame in self.played:
                w.writeframes(frame.data)
