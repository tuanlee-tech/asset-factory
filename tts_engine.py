#!/usr/bin/env python3
"""Sinh audio tiếng Việt + file SRT từ text.

Providers:
  edge    - edge-tts (online, miễn phí)
  zero    - ZeroTTS local (offline, lần đầu tải ~900MB weights)
  vietneu - VieNeu-TTS v3 Turbo (pip install vieneu, chạy CPU torch-free)

Text dài được tách chunk và tổng hợp SONG SONG, sau đó nối lại + ghi SRT
theo thời lượng thật của từng chunk.

Dùng như module:
  from tts_engine import synthesize_all, save_audio_mp3, write_srt, list_voices

CLI:
  python tts_engine.py "Xin chào thế giới."
  python tts_engine.py --file script.txt -o assets/audio/narration.mp3
  python tts_engine.py --provider vietneu --voice "Trúc Ly" "..."
  python tts_engine.py --provider edge --list-voices
"""
from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

OUT_SR = 44100
DEFAULT_CHUNK = 200
DEFAULT_WORKERS = 4
DEFAULT_GAP = 0.25

# ------------------------------------------------------------- chunk + srt

_SENT_SPLIT = re.compile(r"(?<=[.!?…\n])\s+")


def chunk_text(text: str, max_chars: int = DEFAULT_CHUNK) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in parts:
        while len(p) > max_chars:  # câu quá dài: cắt cứng theo từ
            cut = p.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if not cur:
            cur = p
        elif len(cur) + 1 + len(p) <= max_chars:
            cur += " " + p
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def srt_time(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def write_srt(path: Path, segments: list[tuple[float, float, str]]) -> None:
    lines = []
    for i, (st, en, tx) in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(srt_time(st) + " --> " + srt_time(en))
        lines.append(tx)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ audio

def _as_float(arr: np.ndarray) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float64).reshape(-1)
    if a.size and np.abs(a).max() > 1.5:  # int16 range
        a = a / 32768.0
    return a.astype(np.float32)


def resample_pcm(pcm: np.ndarray, src_sr: int, dst_sr: int = OUT_SR) -> np.ndarray:
    if src_sr == dst_sr:
        return _as_float(pcm)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(src_sr),
           "-ac", "1", "-i", "-", "-ar", str(dst_sr), "-ac", "1",
           "-f", "f32le", "-"]
    raw = _as_float(pcm).tobytes()
    out = subprocess.run(cmd, input=raw, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def decode_mp3(data: bytes, dst_sr: int = OUT_SR) -> np.ndarray:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", "-", "-ar", str(dst_sr),
           "-ac", "1", "-f", "f32le", "-"]
    out = subprocess.run(cmd, input=data, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def save_wav(path: Path, pcm: np.ndarray, sr: int = OUT_SR) -> None:
    import wave

    a = np.clip(_as_float(pcm), -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((a * 32767).astype(np.int16).tobytes())


def save_audio_mp3(path: Path, pcm: np.ndarray, sr: int = OUT_SR) -> None:
    import tempfile

    a = np.clip(_as_float(pcm), -1.0, 1.0)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        tmp = Path(t.name)
    try:
        save_wav(tmp, a, sr)
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(tmp),
               "-codec:a", "libmp3lame", "-q:a", "3", str(path)]
        subprocess.run(cmd, check=True)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------- providers

_edge_voices_cache: list[dict] | None = None


async def _list_edge_vi() -> list[dict]:
    global _edge_voices_cache
    if _edge_voices_cache is not None:
        return _edge_voices_cache
    import edge_tts

    vs = await edge_tts.list_voices()
    out = [{"id": v.get("ShortName") or v["Name"],
            "label": (v.get("ShortName") or v["Name"]) + " (" + v.get("Gender", "") + ")"}
           for v in vs if v.get("Locale", "").startswith("vi-")]
    _edge_voices_cache = out or [{"id": "vi-VN-NamMinhNeural", "label": "vi-VN-NamMinhNeural"}]
    return _edge_voices_cache


async def _edge_chunk(text: str, voice: str, rate: str, pitch: str,
                      sem: asyncio.Semaphore) -> np.ndarray:
    import edge_tts

    async with sem:
        comm = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        data = b"".join([c["data"] async for c in comm.stream()
                         if c.get("type") == "audio" and c.get("data")])
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, decode_mp3, data)


async def _synth_edge(chunks: list[str], voice: str, rate: str, pitch: str,
                      workers: int) -> list[np.ndarray]:
    sem = asyncio.Semaphore(max(1, workers))
    return list(await asyncio.gather(
        *(_edge_chunk(c, voice, rate, pitch, sem) for c in chunks)))


_vieneu_lock = threading.Lock()
_vieneu_inst = None


def get_vieneu():
    global _vieneu_inst
    with _vieneu_lock:
        if _vieneu_inst is None:
            from vieneu import Vieneu

            _vieneu_inst = Vieneu()
        return _vieneu_inst


def list_vietneu_voices() -> list[dict]:
    t = get_vieneu()
    return [{"id": vid, "label": label} for label, vid in t.list_preset_voices()]


def _synth_vietneu(chunks: list[str], voice: str, workers: int) -> list[np.ndarray]:
    t = get_vieneu()

    def one(c: str) -> np.ndarray:
        return resample_pcm(t.infer(text=c, voice=voice), 48000)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(one, chunks))


_zero_lock = threading.Lock()
_zero_inst = None


def get_zero():
    global _zero_inst
    with _zero_lock:
        if _zero_inst is None:
            from zerotts import ZeroTTS

            _zero_inst = ZeroTTS.from_pretrained("zeroweight-ai/ZeroTTS")
        return _zero_inst


def list_zero_voices() -> list[dict]:
    try:
        t = get_zero()
        return [{"id": v, "label": str(v)} for v in t.list_voices()]
    except Exception:
        return [{"id": "maichi", "label": "maichi (mặc định)"}]


def _synth_zero(chunks: list[str], voice: str, workers: int) -> list[np.ndarray]:
    t = get_zero()
    sr = int(getattr(t, "sample_rate", 48000))

    def one(c: str) -> np.ndarray:
        out = t.synthesize(c, voice=voice)
        audio = out["audio"] if isinstance(out, dict) else out
        return resample_pcm(audio, sr)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(one, chunks))


def list_voices(provider: str) -> list[dict]:
    if provider == "edge":
        return asyncio.run(_list_edge_vi())
    if provider == "zero":
        return list_zero_voices()
    if provider == "vietneu":
        return list_vietneu_voices()
    raise ValueError("provider không hỗ trợ: " + provider)


# ---------------------------------------------------------------- pipeline

# Priority: vietneu → zero → edge (edge = fallback cuối, chỉ khi local fail)
PROVIDER_PRIORITY = ["vietneu", "zero", "edge"]


def _apply_rate_atempo(pcm: np.ndarray, sr: int, rate: str) -> np.ndarray:
    """Đổi tốc độ bằng ffmpeg atempo cho provider không nhận rate trực tiếp."""
    m = re.search(r"([+-]?\d+)", rate or "")
    if not m:
        return pcm
    pct = int(m.group(1))
    if pct == 0:
        return pcm
    tempo = max(0.5, min(2.0, 1.0 + pct / 100.0))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(sr),
           "-ac", "1", "-i", "-", "-filter:a", "atempo=%.3f" % tempo,
           "-f", "f32le", "-"]
    raw = _as_float(pcm).tobytes()
    out = subprocess.run(cmd, input=raw, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def synthesize_all(text: str, provider: str = "vietneu", voice: str | None = None,
                   rate: str = "+50%", pitch: str = "+0Hz",
                   max_chars: int = DEFAULT_CHUNK, workers: int = DEFAULT_WORKERS,
                   gap: float = DEFAULT_GAP,
                   fallback: bool = True
                   ) -> tuple[np.ndarray, int, list[tuple[float, float, str]]]:
    """Tổng hợp text dài (chunk song song). Trả về (pcm_44100, sr, segments).

    provider mặc định vietneu. Nếu fallback=True và provider chính fail,
    thử tiếp các provider còn lại theo thứ tự ưu tiên, edge là cuối.
    """
    chunks = chunk_text(text, max_chars)
    if not chunks:
        raise ValueError("Text rỗng")

    def _synth_one(p: str) -> list[np.ndarray]:
        if p == "edge":
            return asyncio.run(_synth_edge(chunks, voice or "vi-VN-NamMinhNeural",
                                           rate, pitch, workers))
        if p == "vietneu":
            parts = _synth_vietneu(chunks, voice or "Hải Đăng", workers)
        elif p == "zero":
            parts = _synth_zero(chunks, voice or "maichi", workers)
        else:
            raise ValueError("provider không hỗ trợ: " + p)
        if rate and rate.strip() not in ("+0%", "0%"):
            parts = [_apply_rate_atempo(a, OUT_SR, rate) for a in parts]
        return parts

    providers = [provider] if not fallback else \
        [provider] + [p for p in PROVIDER_PRIORITY if p != provider]
    parts = None
    last_exc = None
    for p in providers:
        try:
            parts = _synth_one(p)
            if p != provider:
                print("tts fallback: %s -> %s" % (provider, p), flush=True)
            break
        except Exception as exc:
            last_exc = exc
            print("tts provider %s failed: %s" % (p, exc), flush=True)
    if parts is None:
        raise RuntimeError("Tất cả provider TTS đều fail: %s" % last_exc)

    gap_pcm = np.zeros(int(OUT_SR * gap), dtype=np.float32)
    full = []
    segments = []
    t = 0.0
    for i, (pcm, tx) in enumerate(zip(parts, chunks)):
        a = _as_float(pcm)
        if i > 0:
            full.append(gap_pcm)
            t += gap
        full.append(a)
        dur = len(a) / OUT_SR
        segments.append((t, t + dur, tx))
        t += dur
    peak = np.abs(np.concatenate(full)).max() if full else 0.0
    if peak > 0:
        full = [a / peak * 0.95 for a in full]
    return np.concatenate(full), OUT_SR, segments


def main() -> int:
    p = argparse.ArgumentParser(description="Sinh audio tiếng Việt + SRT (chunk song song)")
    p.add_argument("text", nargs="?", help="Cần nói (hoặc dùng --file)")
    p.add_argument("--file", help="Đọc text từ file UTF-8")
    p.add_argument("-o", "--out", help="File output (.mp3/.wav). Mặc định assets/audio/<timestamp>.mp3")
    p.add_argument("--provider", choices=["edge", "zero", "vietneu"], default="vietneu")
    p.add_argument("--voice", default=None, help="ID voice (xem --list-voices)")
    p.add_argument("--rate", default="+50%", help="edge: ví dụ +10%, -20%")
    p.add_argument("--pitch", default="+0Hz", help="edge: ví dụ -10Hz, +20Hz")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Số chunk chạy song song")
    p.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, help="Độ dài tối đa mỗi chunk (ký tự)")
    p.add_argument("--gap", type=float, default=DEFAULT_GAP, help="Nghỉ giữa chunk (giây)")
    p.add_argument("--no-srt", action="store_true", help="Không ghi file .srt kèm theo")
    p.add_argument("--list-voices", action="store_true")
    args = p.parse_args()

    if args.list_voices:
        for v in list_voices(args.provider):
            print(v["id"] + "\t" + v["label"])
        return 0

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text
    else:
        p.error("Cần truyền text hoặc --file")
    if not text:
        p.error("Text rỗng")

    out = Path(args.out) if args.out else None
    if out is None:
        from datetime import datetime

        ext = "mp3" if args.provider != "zero" else "wav"
        out = Path("assets/audio") / (datetime.now().strftime("%Y%m%d_%H%M%S") + "." + ext)
    out.parent.mkdir(parents=True, exist_ok=True)

    print("provider=%s voice=%s workers=%d" % (args.provider, args.voice or "default", args.workers))
    pcm, sr, segments = synthesize_all(text, args.provider, args.voice,
                                       args.rate, args.pitch,
                                       args.chunk, args.workers, args.gap)
    print("chunks=%d duration=%.1fs" % (len(segments), len(pcm) / sr))
    if out.suffix.lower() == ".wav":
        save_wav(out, pcm, sr)
    else:
        save_audio_mp3(out, pcm, sr)
    print("OK -> " + str(out) + " (%d bytes)" % out.stat().st_size)
    if not args.no_srt:
        srt = out.with_suffix(".srt")
        write_srt(srt, segments)
        print("SRT -> " + str(srt))
    return 0


if __name__ == "__main__":
    sys.exit(main())