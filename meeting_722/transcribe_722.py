from __future__ import annotations

import json
from pathlib import Path

VIDEO = Path(r"E:\722.mp4")
OUT_DIR = Path(r"D:\test2\meeting_722")
AUDIO = OUT_DIR / "722.wav"
CHUNK_DIR = OUT_DIR / "chunks"
TRANSCRIPT_JSON = OUT_DIR / "722_transcript.json"
TRANSCRIPT_TXT = OUT_DIR / "722_transcript.txt"


def fmt_time(sec: float) -> str:
    minutes = int(sec // 60)
    seconds = int(sec % 60)
    return f"{minutes:02d}:{seconds:02d}"


def transcribe() -> list[dict]:
    if TRANSCRIPT_JSON.exists() and TRANSCRIPT_JSON.stat().st_size > 0:
        return json.loads(TRANSCRIPT_JSON.read_text(encoding="utf-8"))

    from faster_whisper import WhisperModel

    chunk_files = sorted(CHUNK_DIR.glob("chunk_*.wav"))
    if not chunk_files:
        chunk_files = [AUDIO]

    model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4)
    rows: list[dict] = []
    lines: list[str] = []
    info = None
    for chunk_idx, chunk in enumerate(chunk_files):
        offset = chunk_idx * 480.0
        print(f"transcribing {chunk.name} offset={fmt_time(offset)}", flush=True)
        segments, info = model.transcribe(
            str(chunk),
            language="zh",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=(
                "以下是中文会议录音，讨论内容可能涉及加密货币量化交易系统、"
                "AI 提示词、开仓准确率、交易策略、风险控制、实盘和模拟盘。"
            ),
        )
        for seg in segments:
            text = seg.text.strip()
            start = offset + seg.start
            end = offset + seg.end
            row = {"start": round(start, 2), "end": round(end, 2), "text": text}
            rows.append(row)
            if text:
                lines.append(f"[{fmt_time(start)}] {text}")
        TRANSCRIPT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        TRANSCRIPT_TXT.write_text("\n".join(lines), encoding="utf-8")
        print(f"saved progress: {len(rows)} segments", flush=True)

    TRANSCRIPT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    TRANSCRIPT_TXT.write_text("\n".join(lines), encoding="utf-8")
    if info is not None:
        print(f"language={info.language} prob={info.language_probability:.2f}")
    print(f"saved {TRANSCRIPT_TXT}")
    return rows


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not VIDEO.exists():
        raise SystemExit(f"missing video: {VIDEO}")
    if not AUDIO.exists():
        raise SystemExit(f"missing audio: {AUDIO}; extract it with ffmpeg first")
    transcribe()
