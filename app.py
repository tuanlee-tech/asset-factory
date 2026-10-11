#!/usr/bin/env python3
"""Assets Factory - web UI chạy cục bộ.

Chạy:  pip install -r requirements.txt
       python app.py  (mở http://127.0.0.1:5000)

Chức năng THẬT: xử lý watermark video (video_dewm.py) + ảnh (image_dewm.py).
Chức năng MÔ PHỎNG (placeholder): TTS, Studio ghép video.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from flask import (Flask, jsonify, render_template, request, send_file,
                   send_from_directory)

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "uploads"
VISUAL_DIR = ROOT / "VISUAL"
AUDIO_DIR = ROOT / "assets" / "audio"
VIDEO_DIR = ROOT / "assets" / "video"

for _d in (UPLOAD_DIR, VISUAL_DIR, AUDIO_DIR, VIDEO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3  # 2 GB (video)

ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".webm"}
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".ogg"}

DIRS = {"visual": VISUAL_DIR, "video": VIDEO_DIR,
        "audio": AUDIO_DIR, "uploads": UPLOAD_DIR}

def _npx_cmd(*args: str) -> list[str]:
    # Windows: npx là shim .cmd/.ps1 nên phải chạy qua cmd;
    # Linux/macOS: gọi trực tiếp binary trên PATH.
    if os.name == "nt":
        return ["cmd", "/c", "npx", *args]
    return ["npx", *args]


# ---------------------------------------------------------------- jobs (video: chay lau -> background thread)

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def _resolve(kind: str, name: str) -> Path:
    if kind not in DIRS:
        raise ValueError("kind không hợp lệ: " + kind)
    p = DIRS[kind] / Path(name).name  # chan path traversal
    if not p.exists():
        raise FileNotFoundError("Không thấy file: " + kind + "/" + Path(name).name)
    return p


def _file_info(kind: str, p: Path) -> dict:
    return {"kind": kind, "name": p.name, "size": p.stat().st_size,
            "url": "/f/" + kind + "/" + p.name}


HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "http://%s:%d" % (HOST, PORT))


def _public_url(kind: str, name: str) -> str:
    from urllib.parse import quote

    return PUBLIC_BASE + "/f/" + kind + "/" + quote(Path(name).name)


def probe_upload(p: Path) -> dict:
    """Đo meta file vừa upload: duration ảnh/video/audio + kích thước."""
    suf = p.suffix.lower()
    try:
        if suf in ALLOWED_IMAGE:
            from PIL import Image

            with Image.open(p) as im:
                return {"width": im.width, "height": im.height}
        if suf in ALLOWED_VIDEO or suf in ALLOWED_AUDIO:
            try:
                from video_dewm import probe as _vprobe

                m = _vprobe(p)
                return {"durationSec": round(m["duration"], 2),
                        "width": m["width"], "height": m["height"],
                        "fps": round(m["fps"], 2)}
            except Exception:
                pass
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(p)],
                capture_output=True, text=True, check=True).stdout
            return {"durationSec": round(float(json.loads(out)["format"]["duration"]), 2)}
    except Exception:
        pass
    return {}


def _run_video_job(job_id: str, src: Path, args: dict) -> None:
    cmd = [sys.executable, str(ROOT / "video_dewm.py"), str(src)]
    if args.get("out_name"):
        cmd += ["-o", str(VIDEO_DIR / Path(args["out_name"]).name)]
    if args.get("crf") is not None and not args.get("lossless"):
        cmd += ["--crf", str(int(args["crf"]))]
    if args.get("lossless"):
        cmd += ["--lossless"]
    if args.get("preset"):
        cmd += ["--preset", str(args["preset"])]
    if args.get("rect"):
        cmd += ["--rect", str(args["rect"])]
    if args.get("gain") is not None and str(args["gain"]) != "":
        cmd += ["--gain", str(float(args["gain"]))]
    if args.get("sample_frames"):
        cmd += ["--sample-frames", str(int(args["sample_frames"]))]
    if args.get("detect_only"):
        cmd += ["--detect-only"]
    if args.get("verify"):
        cmd += ["--verify"]

    with jobs_lock:
        jobs[job_id]["cmd"] = " ".join(cmd)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                cwd=str(ROOT), bufsize=1)
        lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            with jobs_lock:
                jobs[job_id]["log"] = "\n".join(lines[-200:])
        rc = proc.wait()
        with jobs_lock:
            job = jobs[job_id]
            job["log"] = "\n".join(lines[-200:])
            if rc == 0:
                job["status"] = "done"
                if args.get("out_name"):
                    out = VIDEO_DIR / Path(args["out_name"]).name
                elif args.get("detect_only"):
                    out = None
                else:
                    out = VIDEO_DIR / (src.stem + "_clean.mp4")
                if out is not None and out.exists():
                    job["result"] = _file_info("video", out)
                else:
                    job["result"] = None
            else:
                job["status"] = "error"
                job["error"] = "video_dewm.py exit code " + str(rc) + " (xem log)"
    except Exception as exc:  # pragma: no cover
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(exc)


# ---------------------------------------------------------------- pages & files

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/assets")
def assets():
    out: dict[str, list] = {}
    for kind, d in DIRS.items():
        files = []
        for p in sorted(d.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                files.append(_file_info(kind, p))
        out[kind] = files
    return jsonify(out)


@app.get("/f/<kind>/<name>")
def serve_file(kind: str, name: str):
    if kind not in DIRS:
        return jsonify({"error": "kind không hợp lệ"}), 404
    return send_from_directory(DIRS[kind], Path(name).name, as_attachment=False)


@app.post("/api/download/zip")
def download_zip():
    """Gói nhiều file đã xử lý thành 1 ZIP để tải."""
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"error": "Danh sách file rỗng"}), 400
    buf = io.BytesIO()
    used: set[str] = set()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in items:
            try:
                src = _resolve(it.get("kind", "visual"), it.get("name", ""))
            except (ValueError, FileNotFoundError):
                continue
            nm = src.name
            if nm in used:
                nm = src.stem + "_" + str(count) + src.suffix
            used.add(nm)
            zf.write(src, arcname=nm)
            count += 1
    if not count:
        return jsonify({"error": "Không tìm thấy file nào để đóng gói"}), 404
    buf.seek(0)
    name = data.get("zipname") or "assets-factory.zip"
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=Path(name).stem + ".zip")


@app.post("/api/upload")
def upload():
    target = request.form.get("target", "uploads")
    if target not in DIRS:
        return jsonify({"error": "target không hợp lệ"}), 400
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "Thiếu file"}), 400
    dest = DIRS[target] / Path(f.filename).name
    f.save(dest)
    info = _file_info(target, dest)
    info["meta"] = probe_upload(dest)
    return jsonify(info)


# ---------------------------------------------------------------- watermark: ANH (that, chay nhanh)

@app.post("/api/watermark/image")
def watermark_image():
    from image_dewm import clean_file  # import cham de app van chay duoc khi thieu Pillow

    data = request.get_json(force=True, silent=True) or {}
    try:
        src = _resolve(data.get("kind", "visual"), data.get("name", ""))
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 404
    if src.suffix.lower() not in ALLOWED_IMAGE:
        return jsonify({"error": "Không phải file ảnh hỗ trợ"}), 400
    try:
        info = clean_file(
            src,
            thresh=float(data.get("thresh", 12.0)),
            rect=data.get("rect") or None,
            quality=int(data.get("quality", 100)),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    out = Path(info["dst"])
    # out luon nam cung thu muc voi src -> suy kind tu parent
    out_kind = next((k for k, d in DIRS.items() if out.parent == d), "uploads")
    return jsonify({
        "out": _file_info(out_kind, out),
        "bbox": info["bbox"],
        "fill_rect": info["fill_rect"],
        "src": _file_info(data.get("kind", "visual"), src),
    })


# ---------------------------------------------------------------- watermark: VIDEO (that, chay nen)

@app.post("/api/watermark/video")
def watermark_video():
    data = request.get_json(force=True, silent=True) or {}
    try:
        src = _resolve(data.get("kind", "video"), data.get("name", ""))
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 404
    if src.suffix.lower() not in ALLOWED_VIDEO:
        return jsonify({"error": "Không phải file video hỗ trợ"}), 400
    job_id = uuid.uuid4().hex[:8]
    with jobs_lock:
        jobs[job_id] = {"id": job_id, "kind": "video", "status": "running",
                        "src": _file_info(data.get("kind", "video"), src),
                        "log": "", "result": None, "error": None}
    t = threading.Thread(target=_run_video_job, args=(job_id, src, data), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "Không thấy job"}), 404
    return jsonify(job)


# ---------------------------------------------------------------- placeholder: TTS that

@app.get("/api/tts/voices")
def tts_voices():
    from tts_engine import list_voices

    provider = request.args.get("provider", "vietneu")
    try:
        return jsonify(list_voices(provider))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _run_tts_job(job_id: str, args: dict) -> None:
    from tts_engine import synthesize_all, save_audio_mp3, save_wav, write_srt

    def log(msg: str) -> None:
        with jobs_lock:
            jobs[job_id]["log"] = (jobs[job_id]["log"] + "\n" + msg).strip()[-4000:]

    try:
        provider = args.get("provider", "vietneu")
        log("provider=" + provider + " voice=" + str(args.get("voice") or "default"))
        pcm, sr, segments = synthesize_all(
            args.get("text", ""), provider,
            args.get("voice") or None,
            args.get("rate", "+50%"), args.get("pitch", "+0Hz"),
            workers=int(args.get("workers", 4)),
        )
        dur = len(pcm) / sr
        log("chunks=%d duration=%.1fs" % (len(segments), dur))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fmt = (args.get("format") or "mp3").lower()
        audio = AUDIO_DIR / (ts + "." + ("wav" if fmt == "wav" else "mp3"))
        if fmt == "wav":
            save_wav(audio, pcm, sr)
        else:
            save_audio_mp3(audio, pcm, sr)
        srt = audio.with_suffix(".srt")
        write_srt(srt, segments)
        log("OK -> " + audio.name + " + " + srt.name)
        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["result"] = {
                "audio": _file_info("audio", audio),
                "srt": _file_info("audio", srt),
                "durationSec": round(dur, 2),
                "chunks": len(segments),
            }
    except Exception as exc:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(exc)


@app.post("/api/tts")
def tts():
    data = request.get_json(force=True, silent=True) or {}
    if not (data.get("text") or "").strip():
        return jsonify({"error": "Thiếu text"}), 400
    if data.get("provider", "vietneu") not in ("edge", "zero", "vietneu"):
        return jsonify({"error": "provider không hỗ trợ"}), 400
    job_id = uuid.uuid4().hex[:8]
    with jobs_lock:
        jobs[job_id] = {"id": job_id, "kind": "tts", "status": "running",
                        "log": "", "result": None, "error": None}
    threading.Thread(target=_run_tts_job, args=(job_id, data), daemon=True).start()
    return jsonify({"job_id": job_id})


PREVIEW_TEXT = "Xin chào! Đây là giọng đọc mẫu từ Assets Factory."


@app.post("/api/tts/preview")
def tts_preview():
    """Nghe thử giọng: 1 câu mẫu cố định, sinh sync (ngắn ~3s)."""
    data = request.get_json(force=True, silent=True) or {}
    provider = data.get("provider", "vietneu")
    if provider not in ("edge", "zero", "vietneu"):
        return jsonify({"error": "provider không hỗ trợ"}), 400
    try:
        from tts_engine import synthesize_all, save_audio_mp3, save_wav

        pcm, sr, segments = synthesize_all(
            PREVIEW_TEXT, provider,
            data.get("voice") or None,
            data.get("rate", "+50%"), data.get("pitch", "+0Hz"),
            workers=1, gap=0.0,
        )
        dur = len(pcm) / sr
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        audio = AUDIO_DIR / ("preview_" + ts + ".mp3")
        save_audio_mp3(audio, pcm, sr)
        return jsonify({"audio": _file_info("audio", audio),
                        "durationSec": round(dur, 2),
                        "text": PREVIEW_TEXT})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ------------------------------------------------------- Studio: Remotion render

def _run_render_job(job_id: str, args: dict) -> None:
    def log(msg: str) -> None:
        with jobs_lock:
            jobs[job_id]["log"] = (jobs[job_id]["log"] + "\n" + msg).strip()[-8000:]

    try:
        clips = args.get("clips") or []
        if not clips:
            raise ValueError("Timeline trống")
        props = {
            "clips": [{
                "name": c.get("name") or ("clip-" + str(i)),
                "kind": "video" if str(c.get("kind", "")).lower().startswith("v") else "image",
                "src": c["src"] if str(c.get("src", "")).startswith("http")
                else _public_url(c.get("kind", "uploads"), c.get("name", "")),
                "durationSec": float(c.get("durationSec") or 3.0),
            } for i, c in enumerate(clips)],
            "audioSrc": None,
            "bg": args.get("bg", "#101828"),
        }
        au = args.get("audio")
        if au:
            props["audioSrc"] = au["src"] if str(au.get("src", "")).startswith("http") \
                else _public_url(au.get("kind", "audio"), au.get("name", ""))
        props_path = UPLOAD_DIR / (job_id + "_props.json")
        props_path.write_text(json.dumps(props), encoding="utf-8")
        out_name = Path(args.get("outName") or "factorycut").stem
        out = VIDEO_DIR / (out_name + ".mp4")
        cmd = _npx_cmd("remotion", "render", "FactoryCut",
                       "--props=" + str(props_path), str(out))
        log("$ " + " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, cwd=str(ROOT / "remotion"), bufsize=1)
        lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            with jobs_lock:
                jobs[job_id]["log"] = "\n".join(lines[-60:])
        rc = proc.wait()
        props_path.unlink(missing_ok=True)
        with jobs_lock:
            if rc == 0 and out.exists():
                jobs[job_id]["status"] = "done"
                jobs[job_id]["log"] = "\n".join(lines[-60:])
                jobs[job_id]["result"] = _file_info("video", out)
            else:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["log"] = "\n".join(lines[-60:])
                jobs[job_id]["error"] = "render exit code " + str(rc)
    except Exception as exc:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(exc)


@app.post("/api/studio/render")
def studio_render():
    data = request.get_json(force=True, silent=True) or {}
    if not (data.get("clips") or []):
        return jsonify({"error": "Timeline trống"}), 400
    job_id = uuid.uuid4().hex[:8]
    with jobs_lock:
        jobs[job_id] = {"id": job_id, "kind": "render", "status": "running",
                        "log": "", "result": None, "error": None}
    threading.Thread(target=_run_render_job, args=(job_id, data), daemon=True).start()
    return jsonify({"job_id": job_id})


def _preload_models() -> None:
    # Nạp trước model nặng (VieNeu) ở nền để lần fetch voices đầu không treo.
    try:
        from tts_engine import get_vieneu

        get_vieneu()
        print("preload: vietneu ready", flush=True)
    except Exception as exc:
        print("preload: vietneu failed: " + str(exc), flush=True)


if __name__ == "__main__":
    threading.Thread(target=_preload_models, daemon=True).start()
    app.run(host=HOST, port=PORT, debug=True)
