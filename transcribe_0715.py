from pathlib import Path

from faster_whisper import WhisperModel


VIDEO_PATH = r"E:\715.mp4"
OUT_DIR = Path(__file__).resolve().parent
TXT_PATH = OUT_DIR / "0715_transcript.txt"
SRT_PATH = OUT_DIR / "0715_transcript.srt"


def fmt_ts(seconds: float, sep: str = ",") -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def main() -> None:
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        VIDEO_PATH,
        language="zh",
        vad_filter=True,
        beam_size=5,
        best_of=5,
        condition_on_previous_text=True,
    )

    lines = []
    srt_blocks = []
    for idx, segment in enumerate(segments, 1):
        text = segment.text.strip()
        if not text:
            continue
        start = fmt_ts(segment.start, ".")
        end = fmt_ts(segment.end, ".")
        lines.append(f"[{start} - {end}] {text}")
        srt_blocks.append(
            f"{idx}\n{fmt_ts(segment.start)} --> {fmt_ts(segment.end)}\n{text}\n"
        )

    header = (
        f"language={info.language} probability={info.language_probability:.4f} "
        f"duration={info.duration:.2f}s\n\n"
    )
    TXT_PATH.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    SRT_PATH.write_text("\n".join(srt_blocks), encoding="utf-8")
    print(f"Wrote {TXT_PATH}")
    print(f"Wrote {SRT_PATH}")


if __name__ == "__main__":
    main()
