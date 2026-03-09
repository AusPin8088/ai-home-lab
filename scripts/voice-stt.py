#!/usr/bin/env python3
"""
Local Whisper STT helper for AI Home Lab voice bridge.

Records short microphone audio and returns one JSON line:
{"ok": true, "text": "...", "lang": "en", "duration": 5}
or
{"ok": false, "error": "..."}
"""

from __future__ import annotations

import argparse
import json
import tempfile
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record mic audio and transcribe with faster-whisper.")
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--model", type=str, default="small")
    parser.add_argument("--device", type=str, default="cpu")
    parser.add_argument("--compute-type", type=str, default="int8")
    parser.add_argument("--languages", type=str, default="en,ms,zh")
    return parser.parse_args()


def write_wav(path: Path, pcm: np.ndarray, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def main() -> int:
    args = parse_args()
    timeout_seconds = max(1.0, min(15.0, float(args.timeout_seconds)))
    sample_rate = int(args.sample_rate)
    language_hints = [p.strip().lower() for p in str(args.languages).split(",") if p.strip()]

    try:
        frames = int(timeout_seconds * sample_rate)
        audio = sd.rec(
            frames,
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocking=True,
        )
        audio = np.asarray(audio).reshape(-1)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"audio capture failed: {exc}"}, ensure_ascii=True))
        return 1

    try:
        model = WhisperModel(
            args.model,
            device=args.device,
            compute_type=args.compute_type,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"failed to load whisper model: {exc}"}, ensure_ascii=True))
        return 1

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    try:
        write_wav(wav_path, audio, sample_rate)
        segments, info = model.transcribe(
            str(wav_path),
            beam_size=1,
            vad_filter=True,
            language=None,
        )
        text = " ".join(seg.text.strip() for seg in segments if seg.text).strip()
        lang = (getattr(info, "language", "") or "").lower().strip()
        if not lang:
            lang = "en"
        if language_hints and lang not in language_hints:
            # Keep detected language but mark unknown language family as-is.
            pass

        if not text:
            print(json.dumps({"ok": False, "error": "no speech recognized"}, ensure_ascii=True))
            return 1

        print(
            json.dumps(
                {
                    "ok": True,
                    "text": text,
                    "lang": lang,
                    "duration": timeout_seconds,
                },
                ensure_ascii=True,
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"transcription failed: {exc}"}, ensure_ascii=True))
        return 1
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

