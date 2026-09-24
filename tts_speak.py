"""Sinh audio tiếng Việt từ text.

Providers:
  edge   - edge-tts (online, miễn phí, mặc định)
  zero   - ZeroTTS local (cần pip install zerotts, tải ~900MB weights)

Usage:
  python tts_speak.py "Xin chào thế giới."
  python tts_speak.py --file script.txt -o assets/audio/narration.mp3
  python tts_speak.py --provider zero --voice maichi "Hôm nay trời đẹp quá."
  python tts_speak.py --list-voices
"""

import argparse
import asyncio
import sys
from pathlib import Path

DEFAULT_VOICE = "vi-VN-NamMinhNeural"
OUT_DIR = Path("assets/audio")

EDGE_VOICES = {
    "vi-VN-NamMinhNeural": "Giọng nam miền Bắc (mặc định)",
    "vi-VN-HoaiMyNeural": "Giọng nữ miền Nam",
    "vi-VN-NamMinhNeural2": None,
}


async def speak_edge(text: str, out: Path, voice: str, rate: str, pitch: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(out))


def speak_zero(text: str, out: Path, voice: str) -> None:
    from zerotts import ZeroTTS, normalize_vi_text
    from zerotts.chunking import chunk_text, clean_segment_punctuation, normalize_punctuation

    tts = ZeroTTS.from_pretrained("zeroweight-ai/ZeroTTS")
    norm = normalize_vi_text(text)
    segments = [
        clean_segment_punctuation(s)
        for s in chunk_text(normalize_punctuation(norm), max_chunk_sec=15)
    ]

    import numpy as np

    chunks = []
    for seg in segments:
        audio = tts.synthesize(seg, voice=voice or "maichi")
        chunks.append(audio["audio"] if isinstance(audio, dict) else audio)
    merged = np.concatenate([np.asarray(c, dtype="float32").reshape(-1) for c in chunks])
    tts.save_audio(merged, str(out))


def main() -> int:
    p = argparse.ArgumentParser(description="Sinh audio tiếng Việt")
    p.add_argument("text", nargs="?", help="Cần nói (hoặc dùng --file)")
    p.add_argument("--file", help="Đọc text từ file UTF-8")
    p.add_argument("-o", "--out", help="File output (.mp3/.wav). Mặc định assets/audio/<timestamp>.mp3")
    p.add_argument("--provider", choices=["edge", "zero"], default="edge")
    p.add_argument("--voice", default=None, help="Mặc định: vi-VN-NamMinhNeural (edge) / maichi (zero)")
    p.add_argument("--rate", default="+0%", help="edge: ví dụ +10%%, -20%%")
    p.add_argument("--pitch", default="+0Hz", help="edge: ví dụ -10Hz, +20Hz")
    p.add_argument("--list-voices", action="store_true")
    args = p.parse_args()

    if args.list_voices:
        if args.provider == "zero":
            from zerotts import ZeroTTS

            tts = ZeroTTS.from_pretrained("zeroweight-ai/ZeroTTS")
            for v in tts.list_voices():
                print(v)
        else:
            import asyncio as aio
            import edge_tts

            async def list_v():
                vs = await edge_tts.list_voices()
                for v in vs:
                    if v["Locale"].startswith("vi-"):
                        print(f"{v['Name']:35} {v['Gender']}")

            aio.run(list_v())
        return 0

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text
    else:
        p.error("Cần truyền text hoặc --file")

    if not text:
        p.error("Text rỗng")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.out:
        out = Path(args.out)
    else:
        from datetime import datetime

        out = OUT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.{'wav' if args.provider == 'zero' else 'mp3'}"
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.provider == "edge":
        voice = args.voice or DEFAULT_VOICE
        asyncio.run(speak_edge(text, out, voice, args.rate, args.pitch))
    else:
        voice = args.voice or "maichi"
        speak_zero(text, out, voice)

    size = out.stat().st_size
    print(f"OK -> {out} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
