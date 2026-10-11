#!/usr/bin/env python3
"""Video promo Assets Factory — phong cách quảng cáo chuyên nghiệp.

Pipeline:
  1. Screenshot UI thật (headless Edge, deep-link ?tab=)
  2. Voiceover VieNeu "Hải Đăng" @ +50%
  3. Nhạc pad synth + SFX (whoosh / impact / riser) — numpy
  4. Scene encode: camera push/pan rule-of-thirds, lower-third,
     vignette + film-grain (Ken Burns)
  5. Xfade transitions + audio ducking (sidechain) + loudnorm

Chạy:  python make_promo.py
Output: assets/video/assetsfactory_promo.mp4
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PROMO = ROOT / "promo"
PROMO.mkdir(exist_ok=True)
OUT = ROOT / "assets" / "video" / "assetsfactory_promo.mp4"
BASE = "http://127.0.0.1:5000"
FPS = 30
W, H = 1920, 1080
SR = 48000
T_XFADE = 0.55          # thời lượng mỗi transition
VOICE = "Hải Đăng"
RATE = "+50%"

FONT_R = "C\\:/Windows/Fonts/segoeui.ttf"
FONT_B = "C\\:/Windows/Fonts/segoeuib.ttf"
FONT_M = "C\\:/Windows/Fonts/consola.ttf"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

# cam: push_in | pull_out | pan_right | pan_left
# frame: cx/cy = tỉ lệ tâm khung (0..1) để dẫn dắt mắt theo rule-of-thirds
SCENES = [
    {"kind": "title",
     "text": "Assets Factory. Xưởng dựng asset chạy hoàn toàn trên máy của bạn."},
    {"kind": "shot", "tab": "watermark", "img": "wm.png",
     "cam": "push_in", "cx": 0.42, "cy": 0.55,
     "lower": "01 · WATERMARK",
     "text": ("Xóa watermark ảnh và video chỉ với vài cú bấm. "
              "Detect tự động, so sánh trước sau, xử lý hàng loạt. "
              "Tất cả đều cục bộ.")},
    {"kind": "shot", "tab": "tts", "img": "tts.png",
     "cam": "pan_right", "cx": 0.50, "cy": 0.52,
     "lower": "02 · TTS TIẾNG VIỆT",
     "text": ("Tạo giọng nói tiếng Việt tự nhiên. "
              "Nghe thử giọng trước khi dựng, rồi xuất audio kèm phụ đề SRT.")},
    {"kind": "shot", "tab": "studio", "img": "studio.png",
     "cam": "pull_out", "cx": 0.58, "cy": 0.50,
     "lower": "03 · REMOTION STUDIO",
     "text": ("Dựng video bằng Remotion. Ghép clip, thêm nhạc nền, "
              "rồi render MP4 ngay tại đây.")},
    {"kind": "end",
     "text": ("Không upload, không cloud. "
              "Dữ liệu của bạn không bao giờ rời khỏi máy. Assets Factory.")},
]


def run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(c)[:70] for c in cmd[:4]), "...")
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def ensure_server() -> None:
    import urllib.request
    for _ in range(10):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=3).read()
            return
        except Exception:
            time.sleep(1.5)
    raise RuntimeError("Flask server chưa chạy tại " + BASE)


# ---------------------------------------------------------------- 1. screenshots
def screenshots() -> None:
    exe = next((p for p in EDGE_CANDIDATES if Path(p).exists()), None)
    if not exe:
        raise RuntimeError("Không thấy Edge/Chrome headless")
    profile = PROMO / "edge-profile"
    for sc in SCENES:
        if sc["kind"] != "shot":
            continue
        out = PROMO / sc["img"]
        url = BASE + "/?tab=" + sc["tab"]
        cmd = [
            exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--window-size=1600,1000", "--timeout=12000",
            "--user-data-dir=" + str(profile),
            "--screenshot=" + str(out), url,
        ]
        run(cmd, timeout=40)
        if not out.exists() or out.stat().st_size < 30_000:
            run(cmd, timeout=40)
        assert out.exists() and out.stat().st_size > 10_000, "screenshot fail: " + sc["img"]
        print("shot", sc["img"], out.stat().st_size // 1024, "KB")


# ---------------------------------------------------------------- 2. voiceover
def voiceovers() -> list[Path]:
    from tts_engine import synthesize_all, save_audio_mp3

    outs = []
    for i, sc in enumerate(SCENES):
        dst = PROMO / ("s%d.mp3" % i)
        print("vo scene %d (%s) ..." % (i, VOICE), flush=True)
        pcm, sr, _ = synthesize_all(sc["text"], "vietneu", VOICE,
                                    RATE, "+0Hz", workers=2, gap=0.0)
        save_audio_mp3(dst, pcm, sr)
        print("  ->", dst.name, "%.1fs" % (len(pcm) / sr))
        outs.append(dst)
    return outs


def probe_sec(p: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(p)])
    return float(json.loads(r.stdout)["format"]["duration"])


# ---------------------------------------------------------------- 3. music + SFX
def _write_wav(path: Path, stereo_frames: bytes, sr: int = SR) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(stereo_frames)


def _to_i16_stereo(mono: list[float] | list[int], gain: float = 1.0) -> bytes:
    frames = bytearray()
    for v in mono:
        iv = int(max(-1.0, min(1.0, v * gain)) * 32767)
        frames += struct.pack("<hh", iv, iv)
    return bytes(frames)


def make_music(path: Path, total: float) -> None:
    """Pad ambient — chord Am-F-C-G, lowpass one-pole, fade out cuối."""
    chords = [
        [220.00, 261.63, 329.63, 440.00],
        [174.61, 220.00, 261.63, 349.23],
        [261.63, 329.63, 392.00, 523.25],
        [196.00, 246.94, 293.66, 392.00],
    ]
    chord_sec = 5.0
    n_total = int(total * SR) + SR
    out = [0.0] * n_total

    def add_tone(buf, f0, start, dur, amp, detune=1.003):
        n0 = int(start * SR)
        n = int(dur * SR)
        for k in range(n):
            i = n0 + k
            if i >= len(buf):
                break
            t = k / SR
            env = min(t / 1.2, 1.0) * max(0.0, 1.0 - max(0.0, t - (dur - 1.8)) / 1.8)
            v = (math.sin(2 * math.pi * f0 * t)
                 + 0.5 * math.sin(2 * math.pi * f0 * detune * t)
                 + 0.25 * math.sin(2 * math.pi * f0 * 2 * t))
            buf[i] += amp * env * v

    t, ci = 0.0, 0
    while t < total + chord_sec:
        ch = chords[ci % 4]
        for j, f0 in enumerate(ch):
            add_tone(out, f0, t, chord_sec + 0.6, 0.10 - 0.015 * j)
        add_tone(out, ch[-1] * 2, t + 0.05, 1.6, 0.025, 1.0)
        t += chord_sec
        ci += 1

    y, a = 0.0, 0.12
    peak = max(1e-6, max(abs(v) for v in out))
    fade_n = int(2.5 * SR)
    mono = []
    for i, v in enumerate(out):
        y += a * (v - y)
        s = y / peak * 0.8
        if i > n_total - fade_n:
            s *= max(0.0, (n_total - i) / fade_n)
        mono.append(s)
    _write_wav(path, _to_i16_stereo(mono, 0.55))
    print("music ->", path.name, "%.0fs" % total)


def _white_noise(n: int) -> list[float]:
    # LCG — deterministic, không cần numpy
    out = []
    x = 0x2F6E2B1
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append((x / 0x3FFFFFFF) - 1.0)
    return out


def _env_ad(n: int, attack: float, decay_pow: float = 2.0) -> list[float]:
    out = []
    a_n = max(1, int(attack * SR))
    for i in range(n):
        if i < a_n:
            out.append(i / a_n)
        else:
            d = (i - a_n) / max(1, n - a_n)
            out.append(max(0.0, (1.0 - d)) ** decay_pow)
    return out


def sfx_whoosh(dur: float = 0.85) -> list[float]:
    """Noise sweep low→high→low + panning nhẹ — chuyển cảnh."""
    n = int(dur * SR)
    noise = _white_noise(n)
    env = _env_ad(n, 0.15, 1.6)
    out = []
    y1 = y2 = 0.0
    for i in range(n):
        t = i / n
        # center freq sweep: 300 → 4500 → 600 Hz (sinh k)
        fc = 300 + 4200 * math.sin(math.pi * t) ** 1.4
        alpha = 1.0 - math.exp(-2 * math.pi * fc / SR)
        y1 += alpha * (noise[i] - y1)
        out.append(y1 * env[i])
    # normalize
    peak = max(1e-6, max(abs(v) for v in out))
    return [v / peak * 0.9 for v in out]


def sfx_impact(dur: float = 1.1) -> list[float]:
    """Sub boom + noise transient — open / end card."""
    n = int(dur * SR)
    noise = _white_noise(min(n, int(0.08 * SR)))
    out = [0.0] * n
    for i, nz in enumerate(noise):
        out[i] += nz * (1.0 - i / len(noise)) ** 3 * 0.5
    for i in range(n):
        t = i / SR
        f = 90 * math.exp(-t * 2.2) + 35
        out[i] += math.sin(2 * math.pi * f * t) * math.exp(-t * 3.2) * 0.85
    peak = max(1e-6, max(abs(v) for v in out))
    return [v / peak * 0.95 for v in out]


def sfx_riser(dur: float = 1.4) -> list[float]:
    """Rising noise + rising tone — trước end card."""
    n = int(dur * SR)
    noise = _white_noise(n)
    out = []
    y = 0.0
    for i in range(n):
        t = i / n
        fc = 400 + 5000 * t ** 1.8
        alpha = 1.0 - math.exp(-2 * math.pi * fc / SR)
        y += alpha * (noise[i] - y)
        tone = math.sin(2 * math.pi * (220 + 440 * t ** 2) * (i / SR)) * 0.25
        out.append((y * 0.8 + tone) * (t ** 1.5))
    peak = max(1e-6, max(abs(v) for v in out))
    return [v / peak * 0.75 for v in out]


def sfx_tick() -> list[float]:
    """UI click nhỏ."""
    n = int(0.07 * SR)
    noise = _white_noise(n)
    out = [noise[i] * (1.0 - i / n) ** 6 for i in range(n)]
    peak = max(1e-6, max(abs(v) for v in out))
    return [v / peak * 0.35 for v in out]


def make_sfx_track(path: Path, total: float, marks: list[tuple[float, str]]) -> None:
    """Ghi SFX vào stereo track độ dài `total` tại các `marks` (giây, type)."""
    n = int(total * SR) + SR
    buf = [0.0] * n
    for at, kind in marks:
        if kind == "whoosh":
            sig, pan = sfx_whoosh(), 1.0
        elif kind == "impact":
            sig, pan = sfx_impact(), 1.0
        elif kind == "riser":
            sig, pan = sfx_riser(), 1.0
        else:
            sig, pan = sfx_tick(), 0.6
        n0 = max(0, int(at * SR))
        for i, v in enumerate(sig):
            j = n0 + i
            if j < n:
                buf[j] += v * pan
    peak = max(1e-6, max(abs(v) for v in buf))
    _write_wav(path, _to_i16_stereo([v / peak * 0.9 for v in buf]))
    print("sfx ->", path.name, "marks=%d" % len(marks))


# ---------------------------------------------------------------- 4. scenes
def encode(path: Path, args: list[str]) -> None:
    r = run(["ffmpeg", "-y"] + args + [str(path)], timeout=240)
    if r.returncode != 0:
        print(r.stderr[-2500:])
        raise RuntimeError("encode fail: " + path.name)


def _cam_xy_expr(cam: str, cx: float, cy: float, frames: int, strength: float = 0.10):
    """Trả về (z_expr, x_expr, y_expr) cho zoompan."""
    if cam == "pull_out":
        z = "'max(%.4f-%.4f*on/%d,1.0)'" % (1.0 + strength, strength, frames)
    elif cam == "pan_right":
        z = "'%.4f'" % (1.0 + strength)
    elif cam == "pan_left":
        z = "'%.4f'" % (1.0 + strength)
    else:  # push_in
        z = "'min(1+%.4f*on/%d,%.4f)'" % (strength, frames, 1.0 + strength)
    # tâm khung lệch theo rule-of-thirds
    if cam == "pan_right":
        x = "'(iw-iw/zoom)*on/%d'" % frames
    elif cam == "pan_left":
        x = "'(iw-iw/zoom)*(1-on/%d)'" % frames
    else:
        # push/pull: trượt nhẹ về tâm (cx, cy)
        x = "'(iw-iw/zoom)*%.3f'" % cx
    y = "'(ih-ih/zoom)*%.3f'" % cy
    return z, x, y


def _lower_third_alpha(dur: float) -> str:
    """Alpha: fade-in 0.3→0.8s, hold, fade-out 1.2s trước cuối scene."""
    return ("if(lt(t,0.35),0,if(lt(t,0.85),(t-0.35)/0.5,"
            "if(lt(t,%.2f),1,if(lt(t,%.2f),(%.2f-t)/0.8,0))))"
            % (dur - 1.2, dur - 0.4, dur - 0.4))


def build_shot_scene(sc, vo_dur: float, idx: int) -> Path:
    dst = PROMO / ("scene%d.mp4" % idx)
    dur = vo_dur + 0.9
    frames = int(round(dur * FPS))
    img = PROMO / sc["img"]
    z, x, y = _cam_xy_expr(sc.get("cam", "push_in"),
                           sc.get("cx", 0.5), sc.get("cy", 0.5), frames)

    fades = ",fade=t=in:st=0:d=0.45" if idx == 0 else ""

    # lower third
    lt = ""
    if sc.get("lower"):
        a = _lower_third_alpha(dur)
        txt = sc["lower"].replace("'", "")
        lt = (",drawbox=x=72:y=908:w=6:h=52:color=0x8b5cf6:t=fill"
              ":%s" % ("enable='between(t,0.35,%.2f)'" % (dur - 0.4)))
        lt += (",drawtext=fontfile='%s':text='%s':expansion=none:"
               "alpha='%s':fontcolor=white:fontsize=26:x=92:y=918"
               % (FONT_M, txt, a))

    vf = ("[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
          "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x08080b,"
          "setsar=1,"
          "zoompan=z=%s:x=%s:y=%s:d=%d:s=%dx%d:fps=%d%s%s,"
          "vignette=PI/4.2,noise=alls=5:allf=t+u,format=yuv420p[v]"
          % (z, x, y, frames, W, H, FPS, fades, lt))
    encode(dst, [
        "-i", str(img),
        "-filter_complex", vf,
        "-map", "[v]", "-t", "%.3f" % dur,
        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-pix_fmt", "yuv420p",
    ])
    return dst


def build_card_scene(sc, vo_dur: float, idx: int) -> Path:
    dst = PROMO / ("scene%d.mp4" % idx)
    dur = vo_dur + 1.1
    frames = int(round(dur * FPS))
    if sc["kind"] == "title":
        texts = [
            (FONT_B, "Assets Factory", "white", 104, 330),
            (FONT_R, "Xưởng dựng asset chạy 100% cục bộ", "0xa5a5b0", 40, 475),
            (FONT_M, "Watermark · TTS · Studio", "0x818cf8", 30, 615),
        ]
        accents = (",drawbox=x=850:y=555:w=110:h=4:color=0x6366f1:t=fill:enable='between(t,0.4,99)',"
                   "drawbox=x=960:y=555:w=110:h=4:color=0x8b5cf6:t=fill:enable='between(t,0.4,99)'")
        in_fade, out_fade = 0.6, 0.7
    else:
        texts = [
            (FONT_B, "Không upload. Không cloud.", "white", 66, 360),
            (FONT_R, "Dữ liệu không rời khỏi máy bạn", "0xa5a5b0", 40, 480),
            (FONT_M, "python app.py", "0x4cc38a", 34, 590),
        ]
        accents = ""
        in_fade, out_fade = 0.5, 0.9

    # staggered fade-in theo dòng
    draws = []
    for i, (f, t, c, s, yy) in enumerate(texts):
        d0 = 0.25 + i * 0.18
        a = ("if(lt(t,%.2f),0,if(lt(t,%.2f),(t-%.2f)/0.45,"
             "if(lt(t,%.2f),1,if(lt(t,%.2f),(%.2f-t)/0.7,0))))"
             % (d0, d0 + 0.45, d0, dur - out_fade, dur - 0.2, dur - 0.2))
        draws.append(
            "drawtext=fontfile='%s':text='%s':expansion=none:alpha='%s':"
            "fontcolor=%s:fontsize=%d:x=(w-text_w)/2:y=%d"
            % (f, t, a, c, s, yy))
    vf = (",".join(draws) + accents +
          ",fade=t=in:st=0:d=%.2f,fade=t=out:st=%.2f:d=%.2f,"
          "vignette=PI/4.5,format=yuv420p[v]"
          % (in_fade, dur - out_fade, out_fade))

    # camera nhẹ trên title/end: slow push
    z = "'min(1+%.4f*on/%d,1.06)'" % (0.06, frames)
    vf_final = ("[0:v]zoompan=z=%s:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                ":d=%d:s=%dx%d:fps=%d[vz];[vz]%s"
                % (z, frames, W, H, FPS, vf))
    encode(dst, [
        "-f", "lavfi",
        "-i", "color=c=0x08080b:s=%dx%d:r=%d:d=%.3f" % (W, H, FPS, dur),
        "-filter_complex", vf_final,
        "-map", "[v]", "-t", "%.3f" % dur,
        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-pix_fmt", "yuv420p",
    ])
    return dst


# ---------------------------------------------------------------- 5. audio prep
def pad_voice_to(vo: Path, dur: float, delay: float, dst: Path) -> None:
    """VO + delay mở đầu + pad im lặng tới đúng `dur` giây."""
    encode(dst, [
        "-i", str(vo),
        "-af", "adelay=%d|%.0f,apad,atrim=0:%.3f,aresample=48000"
               % (int(delay * 1000), delay * 1000, dur),
        "-ac", "2", "-c:a", "pcm_s16le",
    ])


# ---------------------------------------------------------------- 6. tổng hợp
def main() -> int:
    t0 = time.time()
    print("== 0/6 server ==")
    ensure_server()

    print("== 1/6 screenshot ==")
    screenshots()

    print("== 2/6 voiceover (VieNeu %s @ %s) ==" % (VOICE, RATE))
    vos = voiceovers()
    vo_durs = [probe_sec(p) for p in vos]
    scene_durs = [d + (0.9 if sc["kind"] == "shot" else 1.1)
                  for sc, d in zip(SCENES, vo_durs)]
    total = sum(scene_durs) - T_XFADE * (len(SCENES) - 1)
    print("scene durs:", ["%.1f" % d for d in scene_durs], "final~%.1f" % total)

    print("== 3/6 music + sfx ==")
    music = PROMO / "music.wav"
    make_music(music, total + 1.5)

    # marks: impact mở đầu, riser trước end, whoosh tại mỗi transition
    offsets = []
    acc = 0.0
    for i, d in enumerate(scene_durs):
        if i > 0:
            acc += scene_durs[i - 1] - T_XFADE
            offsets.append(acc)
    marks: list[tuple[float, str]] = [(0.15, "impact"), (0.4, "tick")]
    for i, off in enumerate(offsets):
        marks.append((max(0.0, off - 0.35), "whoosh"))
        marks.append((off + 0.02, "tick"))
    end_start = sum(scene_durs[:-1]) - T_XFADE * (len(SCENES) - 2) \
        if len(SCENES) > 1 else 0.0
    marks.append((max(0.0, end_start - 1.3), "riser"))
    marks.append((end_start + 0.1, "impact"))
    sfx = PROMO / "sfx.wav"
    make_sfx_track(sfx, total + 1.0, marks)

    print("== 4/6 encode scenes ==")
    scenes = []
    for i, (sc, d) in enumerate(zip(SCENES, vo_durs)):
        if sc["kind"] == "shot":
            scenes.append(build_shot_scene(sc, d, i))
        else:
            scenes.append(build_card_scene(sc, d, i))
        print("  scene", i, "ok", flush=True)

    print("== 5/6 voice pads ==")
    vo_pads = []
    for i, (vo, d) in enumerate(zip(vos, scene_durs)):
        pad = PROMO / ("vo_pad%d.wav" % i)
        delay = 0.35 if SCENES[i]["kind"] == "shot" else 0.45
        pad_voice_to(vo, d, delay, pad)
        vo_pads.append(pad)
    vo_list = PROMO / "vo_concat.txt"
    vo_list.write_text("".join("file '%s'\n" % p.name for p in vo_pads),
                       encoding="utf-8")
    vo_full = PROMO / "vo_full.wav"
    encode(vo_full, ["-f", "concat", "-safe", "0", "-i", str(vo_list),
                     "-c", "copy"])

    print("== 6/6 xfade + mix ==")
    # video xfade chain
    n = len(scenes)
    inputs: list[str] = []
    for s in scenes:
        inputs += ["-i", str(s)]
    parts = []
    acc_d = scene_durs[0]
    prev = "[0:v]"
    for k in range(1, n):
        off = acc_d - T_XFADE
        outl = "[vx%d]" % k if k < n - 1 else "[vx]"
        parts.append("%s[%d:v]xfade=transition=%s:duration=%.2f:offset=%.3f%s"
                     % (prev, k, "fade" if k == 1 else
                        ("wipeleft" if k == 2 else
                         ("slideright" if k == 3 else "fadeblack")),
                        T_XFADE, off, outl))
        prev = outl
        acc_d = acc_d + scene_durs[k] - T_XFADE

    # audio: voice + duck music + sfx (inputs: 0..4 video, 5=vo, 6=music, 7=sfx)
    audio_fc = (
        "[5:a]volume=1.0[vo];"
        "[6:a]volume=0.32[m0];"
        "[vo]asplit=2[vo1][sc];"
        "[m0][sc]sidechaincompress=threshold=0.02:ratio=10:attack=40:release=420[mduck];"
        "[vo1][mduck][7:a]amix=inputs=3:duration=first:dropout_transition=2,"
        "alimiter=limit=0.95,loudnorm=I=-14:TP=-1.2:LRA=11[aout]"
    )

    fc = ";".join(parts + [audio_fc])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    r = run(["ffmpeg", "-y"] + inputs + ["-i", str(vo_full),
             "-i", str(music), "-i", str(sfx),
             "-filter_complex", fc,
             "-map", "[vx]", "-map", "[aout]",
             "-t", "%.3f" % total,
             "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "19",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             str(OUT)], timeout=420)
    if r.returncode != 0:
        print(r.stderr[-3000:])
        return 1

    dur = probe_sec(OUT)
    size = OUT.stat().st_size / 1e6
    print("DONE %.1fs %.1fMB in %.0fs" % (dur, size, time.time() - t0))
    print("->", OUT)
    print("->", BASE + "/f/video/" + OUT.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
