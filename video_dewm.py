#!/usr/bin/env python3
"""Xóa watermark (Veo / Gemini) khỏi video KHÔNG làm giảm chất lượng.

Nguyên lý: watermark được Google chồng lên frame bằng alpha blending nên đảo
được một cách toán học (reverse alpha blending), KHÔNG dùng AI inpainting:

    observed = a*LOGO + (1-a)*original
    original = (observed - a*LOGO) / (1-a)

  - Pixel ngoài vùng watermark giữ nguyên (byte-for-byte sau khi decode).
  - Chỉ vùng ROI (vài chục px góc phải-dưới) bị sửa.
  - Audio stream copy: không encode lại -> bit-identical.
  - Video encode đúng 1 lần, CRF 14 (mặc định) hoặc --lossless (CRF 0).

Usage:
  python video_dewm.py input.mp4
  python video_dewm.py input.mp4 -o out.mp4 --crf 14
  python video_dewm.py input.mp4 --detect-only
  python video_dewm.py input.mp4 --verify
  python video_dewm.py input.mp4 --rect 682,1254,23,10 --gain 1.25
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "tools" / "video" / "data"
OUT_DIR = ROOT / "assets" / "video"

LOGO = 255.0
Y_LOGO = 235.0
UV_LOGO = 128.0
ALPHA_MIN = 0.025
# Kẹp alpha theo nền chỉ áp cho pixel không sáng hơn nền quá một nửa quãng
# đường tới logo: ngoài vùng đó coi alpha map là đúng (tránh làm sáng giả
# khi nội dung dưới watermark tối hơn nền).
CLAMP_TAU = 0.5
DEFAULT_CRF = 14
DEFAULT_PRESET = "slow"

# ---------------------------------------------------------------- alpha maps


def load_f32(name: str) -> np.ndarray:
    return np.frombuffer((DATA / name).read_bytes(), dtype="<f4").copy()


def resize_map(src: np.ndarray, sw: int, sh: int, dw: int, dh: int) -> np.ndarray:
    """Area-average resize (bản port của resizeAlphaMapArea trong JS)."""
    if dw <= 0 or dh <= 0:
        return np.zeros(0, dtype=np.float32)
    if sw == dw and sh == dh:
        return src.reshape(sh, sw).astype(np.float32, copy=True)
    src = src.reshape(sh, sw).astype(np.float64)
    out = np.zeros((dh, dw), dtype=np.float32)
    sx, sy = sw / dw, sh / dh
    for y in range(dh):
        y0f, y1f = y * sy, (y + 1) * sy
        y0, y1 = int(np.floor(y0f)), int(np.ceil(y1f))
        wy = np.clip(np.minimum(y1f, np.arange(y0, y1) + 1) - np.maximum(y0f, y0), 0, None)
        for x in range(dw):
            x0f, x1f = x * sx, (x + 1) * sx
            x0, x1 = int(np.floor(x0f)), int(np.ceil(x1f))
            wx = np.clip(np.minimum(x1f, np.arange(x0, x1) + 1) - np.maximum(x0f, x0), 0, None)
            block = src[y0:y1, x0:x1]
            area = wy[:, None] * wx[None, :]
            s = float((block * area).sum())
            a = float(area.sum())
            out[y, x] = s / a if a > 0 else 0.0
    return out.reshape(-1)


def resize_map_area(src: np.ndarray, src_size: int, dst_size: int) -> np.ndarray:
    return resize_map(src, src_size, src_size, dst_size, dst_size)



def enhance_edges(alpha: np.ndarray, strength: float) -> np.ndarray:
    a = np.asarray(alpha, dtype=np.float32)
    if a.ndim != 2 or a.shape[0] < 3 or a.shape[1] < 3:
        return a.astype(np.float32, copy=True)
    if not np.isfinite(strength) or strength <= 0:
        return a.astype(np.float32, copy=True)
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[1:-1, 1:-1] = (
        -a[:-2, :-2] - 2 * a[:-2, 1:-1] - a[:-2, 2:]
        + a[2:, :-2] + 2 * a[2:, 1:-1] + a[2:, 2:]
    )
    gy[1:-1, 1:-1] = (
        -a[:-2, :-2] - 2 * a[1:-1, :-2] - a[2:, :-2]
        + a[:-2, 2:] + 2 * a[1:-1, 2:] + a[2:, 2:]
    )
    grad = np.sqrt(gx * gx + gy * gy)
    mx = float(grad.max())
    if mx <= 0:
        return a.copy()
    edge = np.sqrt(grad / mx)
    return np.minimum(0.99, a + edge * strength).astype(np.float32)


def video_alpha_map(size: int, candidate: dict | None = None) -> np.ndarray:
    profile = "48" if size <= 39 else "96-20260520"
    raw = load_f32(f"alpha_{profile}.bin")
    src_size = int(round(np.sqrt(raw.size)))
    resized = raw if src_size == size else resize_map_area(raw, src_size, size)
    boost = 0.035 if (candidate or {}).get("inset") else 0.045
    return enhance_edges(resized.reshape(size, size), boost).reshape(-1)



def text_templates() -> dict:
    meta = json.loads((DATA / "templates.json").read_text(encoding="utf-8"))
    out = {}
    for tid, m in meta.items():
        det = load_f32(f"veotext_{tid}.bin").reshape(m["height"], m["width"])
        out[tid] = {**m, "detector": det, "base_gain": 0.18}
    return out


# ------------------------------------------------------------------- scoring


def gray_region(frame: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    r = frame[y:y + h, x:x + w].astype(np.float32)
    return (0.2126 * r[..., 0] + 0.7152 * r[..., 1] + 0.0722 * r[..., 2]) / 255.0


def _ncc_flat(a: np.ndarray, b: np.ndarray) -> float:
    if a.size != b.size or a.size == 0:
        return 0.0
    a = a.astype(np.float64).ravel()
    b = b.astype(np.float64).ravel()
    am, bm = a.mean(), b.mean()
    av = float(((a - am) ** 2).sum())
    bv = float(((b - bm) ** 2).sum())
    den = np.sqrt(av * bv)
    if den < 1e-12:
        return 0.0
    return float(((a - am) * (b - bm)).sum() / den)


def sobel_mag(g: np.ndarray) -> np.ndarray:
    out = np.zeros_like(g)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return out
    gx = (
        -g[:-2, :-2] - 2 * g[:-2, 1:-1] - g[:-2, 2:]
        + g[2:, :-2] + 2 * g[2:, 1:-1] + g[2:, 2:]
    )
    gy = (
        -g[:-2, :-2] - 2 * g[1:-1, :-2] - g[2:, :-2]
        + g[:-2, 2:] + 2 * g[1:-1, 2:] + g[2:, 2:]
    )
    out[1:-1, 1:-1] = np.sqrt(gx * gx + gy * gy)
    return out


def diamond_score(frame: np.ndarray, alpha: np.ndarray, cand: dict) -> dict:
    s = cand["size"]
    patch = gray_region(frame, cand["x"], cand["y"], s, s)
    spatial = _ncc_flat(patch, alpha)
    gradient = _ncc_flat(sobel_mag(patch).ravel(), sobel_mag(alpha.reshape(s, s)).ravel())
    confidence = max(0.0, spatial) * 0.35 + max(0.0, gradient) * 0.65
    return {"spatial": spatial, "gradient": gradient, "confidence": confidence}


def rect_ncc_grid(region: np.ndarray, template: np.ndarray, active: float = 0.02) -> np.ndarray:
    """NCC của template với mọi vị trí hợp lệ trong region (vectorised)."""
    H, W = region.shape
    h, w = template.shape
    if H < h or W < w:
        return np.zeros((0, 0), dtype=np.float64)
    mask = np.abs(template) > active
    n = int(mask.sum())
    if n <= 1:
        return np.zeros((H - h + 1, W - w + 1), dtype=np.float64)
    t = template[mask].astype(np.float64)
    tm = t.mean()
    stt = float(((t - tm) ** 2).sum())
    if stt < 1e-12:
        return np.zeros((H - h + 1, W - w + 1), dtype=np.float64)

    from numpy.lib.stride_tricks import sliding_window_view

    win = sliding_window_view(region.astype(np.float64), (h, w))
    sub = win[..., mask]
    sp = sub.sum(axis=-1)
    sp2 = (sub * sub).sum(axis=-1)
    spt = (sub * t).sum(axis=-1)

    cent = spt - tm * sp
    var_p = sp2 - (sp * sp) / n
    den = np.sqrt(np.maximum(var_p, 0.0) * stt)
    out = np.zeros_like(cent)
    ok = den > 1e-12
    out[ok] = cent[ok] / den[ok]
    return out


# ------------------------------------------------------------------ catalog

_REF = ((72, 108, 108), (72, 144, 144))
_REF_W, _REF_H = 1920, 1080


def _round(x: float) -> int:
    return int(np.floor(x + 0.5))


def diamond_candidates(w: int, h: int) -> list[dict]:
    scale = min(w / _REF_W, h / _REF_H)
    out: list[dict] = []

    def add(size, mr, mb, inset=False):
        size = int(min(max(_round(size), 24), min(w, h)))
        mr = int(min(max(_round(mr), 0), w - size))
        mb = int(min(max(_round(mb), 0), h - size))
        cand = {
            "kind": "diamond", "size": size, "width": size, "height": size,
            "margin_right": mr, "margin_bottom": mb,
            "x": w - mr - size, "y": h - mb - size, "inset": inset,
        }
        if cand["x"] >= 0 and cand["y"] >= 0:
            out.append(cand)

    for size, mr, mb in _REF:
        add(size * scale, mr * scale, mb * scale, inset=(mr == 144))

    if (w, h) == (1080, 1920):
        add(72, 108, 108)
        add(72, 144, 144, inset=True)
        return _dedupe(out)
    if (w, h) == (1280, 720):
        add(48, 72, 72)
        add(48, 96, 96, inset=True)
        add(44, 29, 40)
        return _dedupe(out)
    if (w, h) == (720, 1280):
        add(48, 72, 72)
        add(48, 96, 96, inset=True)
        add(24, 48, 48)
        add(35, 102, 96, inset=True)
        add(44, 29, 40)
        return _dedupe(out)
    return _dedupe(out)


def _dedupe(cands: list[dict]) -> list[dict]:
    seen = {}
    for c in cands:
        key = (c["size"], c["margin_right"], c["margin_bottom"])
        seen.setdefault(key, c)
    return list(seen.values())


def text_candidates(w: int, h: int, templates: dict) -> list[dict]:
    out = []
    for tid, t in templates.items():
        tw, th = t["width"], t["height"]
        if tw > w or th > h:
            continue
        radius = max(4, round(min(tw, th) * 0.35))
        for mr in range(max(0, t["margin_right"] - radius), t["margin_right"] + radius + 1):
            if mr + tw > w:
                continue
            for mb in range(max(0, t["margin_bottom"] - radius), t["margin_bottom"] + radius + 1):
                if mb + th > h:
                    continue
                out.append({
                    "kind": "text", "template": tid,
                    "width": tw, "height": th,
                    "margin_right": mr, "margin_bottom": mb,
                    "x": w - mr - tw, "y": h - mb - th,
                })
    return out


# --------------------------------------------------------------- ffmpeg I/O


def probe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    info = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "24/1").partition("/")
    fps = float(num) / float(den or 1)
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": fps,
        "fps_str": f"{num}/{den or 1}",
        "frames": int(v.get("nb_frames") or 0),
        "duration": float(info["format"].get("duration") or 0),
        "pix_fmt": v.get("pix_fmt"),
        "color_range": v.get("color_range"),
        "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
        "size": int(info["format"].get("size") or 0),
    }


def read_frames(path: Path, w: int, h: int, indices: list[int]) -> list[np.ndarray]:
    if not indices:
        return []
    sel = "+".join(f"eq(n\\,{i})" for i in sorted(set(indices)))
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-vf", f"select='{sel}'", "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (w * h * 3)
    return [np.frombuffer(raw[i * w * h * 3:(i + 1) * w * h * 3], dtype=np.uint8)
            .reshape(h, w, 3) for i in range(n)]


def frame_iter_yuv(path: Path, w: int, h: int):
    """Đọc planar yuv420p (không qua RGB -> không mất chất lượng round-trip)."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10 ** 8)
    ysz, csz = w * h, (w // 2) * (h // 2)
    size = ysz + 2 * csz
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            arr = np.frombuffer(buf, dtype=np.uint8)
            Y = arr[:ysz].reshape(h, w).copy()
            U = arr[ysz:ysz + csz].reshape(h // 2, w // 2).copy()
            V = arr[ysz + csz:].reshape(h // 2, w // 2).copy()
            yield Y, U, V
    finally:
        proc.stdout.close()
        proc.wait()


# -------------------------------------------------------- detect + estimate


def detect(frame: np.ndarray) -> dict | None:
    h, w = frame.shape[:2]
    templates = text_templates()
    result = {"diamond": None, "text": None}

    best_d, best_dc = None, -1.0
    for cand in diamond_candidates(w, h):
        s = cand["size"]
        if cand["x"] + s > w or cand["y"] + s > h:
            continue
        alpha = video_alpha_map(s, cand)
        sc = diamond_score(frame, alpha, cand)
        if sc["confidence"] > best_dc:
            best_dc, best_d = sc["confidence"], {**cand, "alpha": alpha, **sc}
    if best_d and best_dc >= 0.18:
        result["diamond"] = best_d

    best_t, best_ncc = None, -1.0
    for tid, t in templates.items():
        tw, th = t["width"], t["height"]
        radius = max(4, round(min(tw, th) * 0.35))
        # vùng quét: bao phủ mọi margin khả dĩ
        x_lo = max(0, w - (t["margin_right"] + radius) - tw)
        x_hi = min(w - tw, w - max(0, t["margin_right"] - radius) - tw)
        y_lo = max(0, h - (t["margin_bottom"] + radius) - th)
        y_hi = min(h - th, h - max(0, t["margin_bottom"] - radius) - th)
        if x_hi < x_lo or y_hi < y_lo:
            continue
        region = gray_region(frame, x_lo, y_lo, x_hi - x_lo + tw, y_hi - y_lo + th)
        grid = rect_ncc_grid(region, t["detector"])
        if grid.size == 0:
            continue
        idx = np.unravel_index(int(np.argmax(grid)), grid.shape)
        val = float(grid[idx])
        if val > best_ncc:
            best_ncc = val
            best_t = {
                **t, "kind": "text", "template": tid,
                "x": x_lo + int(idx[1]), "y": y_lo + int(idx[0]),
                "spatial": val, "confidence": max(0.0, val),
            }
    if best_t and best_ncc >= 0.62:
        result["text"] = best_t

    if not result["diamond"] and not result["text"]:
        return None
    d_conf = (result["diamond"] or {}).get("confidence", 0.0)
    t_conf = (result["text"] or {}).get("confidence", 0.0)
    kind = "diamond" if d_conf >= t_conf else "text"
    return result[kind]


def _alpha_for(pos: dict) -> np.ndarray:
    if pos["kind"] == "diamond":
        return pos["alpha"]
    t = text_templates()[pos["template"]]
    base = t["detector"].astype(np.float32) * t["base_gain"]
    return np.clip(base, 0.0, 0.99).reshape(-1)


def rect_alpha(w: int, h: int) -> np.ndarray:
    raw = load_f32("alpha_96-20260520.bin")
    size = int(round(np.sqrt(raw.size)))
    return enhance_edges(resize_map(raw, size, size, w, h).reshape(h, w), 0.045)


def _seed_for(pos: dict) -> float:
    if pos["kind"] == "diamond":
        return 1.0
    return float(pos.get("observed_seed_scale") or 1.0)


def background_mean(frame: np.ndarray, pos: dict, alpha: np.ndarray, pad: int = 18) -> float:
    h, w = frame.shape[:2]
    x, y, bw, bh = pos["x"], pos["y"], pos["width"], pos["height"]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    luma = 0.2126 * roi[..., 0] + 0.7152 * roi[..., 1] + 0.0722 * roi[..., 2]
    weight = np.ones(luma.shape, dtype=np.float32)
    inb = np.zeros(luma.shape, dtype=bool)
    inb[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = True
    weight[inb] = 0.0
    a = alpha.reshape(bh, bw)
    inner = a <= 0.015
    sub = np.zeros_like(weight, dtype=bool)
    sub[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = inner
    weight[sub] = 0.35
    total = float((weight).sum())
    if total <= 0:
        return float("nan")
    return float((luma * weight).sum() / total)


def background_std(frame: np.ndarray, pos: dict, pad: int = 18) -> float:
    h, w = frame.shape[:2]
    x, y, bw, bh = pos["x"], pos["y"], pos["width"], pos["height"]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    luma = 0.2126 * roi[..., 0] + 0.7152 * roi[..., 1] + 0.0722 * roi[..., 2]
    mask = np.ones(luma.shape, dtype=bool)
    mask[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = False
    sub = luma[mask]
    if sub.size < 4:
        return float("inf")
    return float(sub.std())


def score_gain(frame: np.ndarray, pos: dict, alpha: np.ndarray, gain: float, bg: float) -> float:
    x, y, bw, bh = pos["x"], pos["y"], pos["width"], pos["height"]
    roi = frame[y:y + bh, x:x + bw].astype(np.float32)
    raw = alpha.reshape(bh, bw)
    a = np.where(raw > ALPHA_MIN, np.minimum(raw * gain, 0.99), 0.0)
    valid = a > 0
    if not valid.any():
        return float("nan")
    weight = np.clip(raw * 8.0, 0.0, 1.0)
    den = np.where(valid, 1.0 - a, 1.0)
    restored = (roi - a[..., None] * LOGO) / den[..., None]
    luma = 0.2126 * restored[..., 0] + 0.7152 * restored[..., 1] + 0.0722 * restored[..., 2]
    wts = np.where(valid, weight, 0.0)
    tot = float(wts.sum())
    if tot <= 0:
        return float("nan")
    return float((luma * wts).sum() / tot) - bg


def estimate_gain(frames: list[np.ndarray], pos: dict, alpha: np.ndarray,
                  seed: float) -> tuple[float, dict]:
    """Chọn gain theo khung nền phẳng.

    Objective "restored luma == nền cục bộ" chỉ không thiên lệch khi nội dung
    quanh watermark phẳng. Do đó: sắp frame theo độ lệch chuẩn của vòng nền,
    lấy nửa phẳng nhất, với từng frame tìm zero-crossing của delta rồi lấy
    median. Frame không cắt 0 (nội dung lệch mạnh) bị loại.
    """
    cands = []
    for f in frames:
        bg = background_mean(f, pos, alpha)
        if not np.isfinite(bg):
            continue
        cands.append((background_std(f, pos), bg, f))
    info = {"frames": len(cands), "used": 0, "boundary": False}
    if not cands:
        return seed, info
    cands.sort(key=lambda t: t[0])
    flat = cands[:max(1, (len(cands) + 1) // 2)]

    lo_seed, hi_seed = max(0.2, seed / 4.0), seed * 4.0
    vals = []
    for _, bg, f in flat:
        lo, hi = lo_seed, hi_seed
        d_lo = score_gain(f, pos, alpha, lo, bg)
        d_hi = score_gain(f, pos, alpha, hi, bg)
        if not (np.isfinite(d_lo) and np.isfinite(d_hi)) or d_lo * d_hi > 0:
            continue
        for _ in range(36):
            mid = (lo + hi) / 2.0
            d = score_gain(f, pos, alpha, mid, bg)
            if not np.isfinite(d):
                break
            if d > 0:
                lo = mid
            else:
                hi = mid
        vals.append((lo + hi) / 2.0)
    info["used"] = len(vals)

    if not vals:
        grid = np.linspace(lo_seed, hi_seed, 33)
        best, best_v = seed, float("inf")
        for g in grid:
            ds = [score_gain(f, pos, alpha, float(g), bg) for _, bg, f in flat]
            ds = [d for d in ds if np.isfinite(d)]
            if not ds:
                continue
            v = float(np.median([abs(d) for d in ds]))
            if v < best_v:
                best, best_v = float(g), v
        vals = [best]
        info["used"] = 0

    gain = float(np.median(vals))
    info["boundary"] = (gain <= lo_seed * 1.001) or (gain >= hi_seed * 0.999)
    info["spread"] = float(np.ptp(vals)) if len(vals) > 1 else 0.0
    return gain, info


def build_alpha(raw: np.ndarray, gain: float, bh: int, bw: int) -> np.ndarray:
    a = raw.reshape(bh, bw).astype(np.float32)
    return np.where(a > ALPHA_MIN, np.minimum(a * gain, 0.99), 0.0).astype(np.float32)


def chroma_alpha(A: np.ndarray) -> np.ndarray:
    h, w = A.shape
    return A.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)).astype(np.float32)


def chroma_box(pos: dict) -> tuple[int, int, int, int]:
    x, y, bw, bh = pos["x"], pos["y"], pos["width"], pos["height"]
    return x // 2, y // 2, (x + bw - 1) // 2 - x // 2 + 1, (y + bh - 1) // 2 - y // 2 + 1


def plane_bg(plane: np.ndarray, x: int, y: int, bw: int, bh: int,
             pad: int) -> float:
    """Trung vịnh của vòng quanh box (loại đúng phần box).

    Dùng trung vị để chịu được vài pixel watermark tràn ra ngoài vòng.
    """
    h, w = plane.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    ring = plane[y0:y1, x0:x1]
    mask = np.ones(ring.shape, dtype=bool)
    ry, rx = y - y0, x - x0
    if 0 <= ry < ring.shape[0] and 0 <= rx < ring.shape[1]:
        mask[ry:ry + bh, rx:rx + bw] = False
    sub = ring[mask]
    if sub.size < 4:
        return float("nan")
    return float(np.median(sub))


def restore_yuv(Y: np.ndarray, U: np.ndarray, V: np.ndarray, pos: dict,
                A: np.ndarray, AC: np.ndarray, box: tuple[int, int, int, int],
                y_logo: float = Y_LOGO, uv_logo: float = UV_LOGO) -> None:
    x, y, bw, bh = pos["x"], pos["y"], pos["width"], pos["height"]
    _restore_plane(Y, x, y, bw, bh, A, y_logo,
                   plane_bg(Y, x, y, bw, bh, pad=30))
    cx, cy, cw, ch = box
    aC = AC[cy:cy + ch, cx:cx + cw]
    _restore_plane(U, cx, cy, cw, ch, aC, uv_logo,
                   plane_bg(U, cx, cy, cw, ch, pad=15))
    _restore_plane(V, cx, cy, cw, ch, aC, uv_logo,
                   plane_bg(V, cx, cy, cw, ch, pad=15))


def _restore_plane(plane: np.ndarray, x: int, y: int, bw: int, bh: int,
                   A: np.ndarray, logo: float, bg: float) -> None:
    roi = plane[y:y + bh, x:x + bw].astype(np.float32)
    valid = A > 0
    # Alpha không được vượt mức sáng thêm mà pixel đang có so với nền cục bộ
    # (giả định logo sáng, nội dung ~ nền): nếu không thì pixel nền tinh khiết
    # bị alpha map tràn ra ngoài sẽ bị kéo xuống -> vòng đen quanh watermark.
    # Không áp cho pixel đã sáng hơn nền quá CLAMP_TAU (nội dung có thể khác
    # nền, khi đó alpha map đáng tin hơn).
    if np.isfinite(bg) and abs(logo - bg) >= 8.0:
        frac = (roi - bg) / (logo - bg)
        cap = np.where(frac <= CLAMP_TAU, np.clip(frac, 0.0, 1.0), 1.0)
        a = np.where(valid, np.minimum(A, cap), 0.0)
    else:
        a = np.where(valid, A, 0.0)
    den = np.where(a > 0, 1.0 - a, 1.0)
    out = (roi - a * logo) / den
    out = np.where(a > 0, out, roi)
    plane[y:y + bh, x:x + bw] = np.clip(np.rint(out), 0, 255).astype(np.uint8)


# ------------------------------------------------------------------- encode


def encode(src: Path, dst: Path, meta: dict, crf: int, preset: str) -> list[str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    rng = "pc" if meta.get("color_range") == "pc" else "tv"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-color_range", rng,
        "-s", f"{meta['width']}x{meta['height']}", "-r", meta["fps_str"],
        "-i", "-",
        "-i", str(src),
        "-map", "0:v:0", "-map", "1:a?",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-color_range", rng,
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(dst),
    ]
    return cmd


# ------------------------------------------------------------------ verify


def verify(src: Path, out: Path, meta: dict, pos: dict, sample: int = 24) -> None:
    h, w = meta["height"], meta["width"]
    mask = np.ones((h, w), dtype=bool)
    mask[pos["y"]:pos["y"] + pos["height"], pos["x"]:pos["x"] + pos["width"]] = False

    n = meta["frames"] or int(meta["duration"] * meta["fps"]) or 1
    idx = sorted({int(round(i)) for i in np.linspace(0, n - 1, min(sample, n))})
    a = read_frames(src, w, h, idx)
    b = read_frames(out, w, h, idx)
    if not a or not b or len(a) != len(b):
        print("verify: không đọc được đủ frame để so sánh")
        return

    psnrs, maxdiffs, wm = [], [], []
    for fa, fb in zip(a, b):
        d = fa.astype(np.int16) - fb.astype(np.int16)
        outside = d[mask]
        mse = float((outside.astype(np.float64) ** 2).mean())
        psnrs.append(99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse))
        maxdiffs.append(int(np.abs(d).max()))
        pos_b = detect(fb)
        wm.append((pos_b or {}).get("confidence", 0.0))

    print(f"  frame so sánh        : {len(a)}")
    print(f"  PSNR ngoài ROI (min) : {min(psnrs):.2f} dB")
    print(f"  PSNR ngoài ROI (avg) : {sum(psnrs) / len(psnrs):.2f} dB")
    print(f"  sai lệch tối đa      : {max(maxdiffs)} / 255")
    if wm:
        print(f"  watermark score sau  : max {max(wm):.3f} (trước khi xóa phải >= 0.18/0.62)")


# --------------------------------------------------------------------- main


def main() -> int:
    p = argparse.ArgumentParser(description="Xóa watermark Veo/Gemini khỏi video (giữ nguyên chất lượng)")
    p.add_argument("input")
    p.add_argument("-o", "--out")
    p.add_argument("--crf", type=int, default=DEFAULT_CRF, help="x264 CRF (mặc định 14)")
    p.add_argument("--lossless", action="store_true", help="CRF 0: pixel ngoài ROI giữ nguyên tuyệt đối")
    p.add_argument("--preset", default=DEFAULT_PRESET)
    p.add_argument("--rect", help="Bắt buộc vùng watermark: x,y,w,h")
    p.add_argument("--gain", type=float, help="Bắt buộc alpha gain (bỏ qua tự ước lượng)")
    p.add_argument("--sample-frames", type=int, default=12, help="Số frame dùng để detect/ước lượng gain")
    p.add_argument("--detect-only", action="store_true")
    p.add_argument("--verify", action="store_true", help="Đo PSNR ngoài ROI + watermark score sau khi encode")
    args = p.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"Không thấy file: {src}", file=sys.stderr)
        return 1
    meta = probe(src)
    print(f"input : {src}  {meta['width']}x{meta['height']}  {meta['fps']:.3f}fps  "
          f"{meta['duration']:.2f}s  {meta['pix_fmt']}")

    n_total = meta["frames"] or int(meta["duration"] * meta["fps"]) or 1
    idx = sorted({int(round(i)) for i in np.linspace(max(0, int(n_total * 0.05)),
                                                     max(0, n_total - 1), args.sample_frames)})
    frames = read_frames(src, meta["width"], meta["height"], idx)

    if args.rect:
        x, y, w, h = (int(v) for v in args.rect.split(","))
        det = {"kind": "rect", "x": x, "y": y, "width": w, "height": h,
               "size": max(w, h), "confidence": 1.0}
        alpha = rect_alpha(w, h)
        print(f"forced rect: {x},{y},{w},{h}")
    else:
        scores: list[tuple] = []
        for f in frames:
            d = detect(f)
            if d:
                scores.append((d, d.get("confidence", 0.0)))

        if not scores:
            print("Không phát hiện watermark. Dùng --rect x,y,w,h nếu bạn chắc chắn nó có.")
            return 2
        det = max(scores, key=lambda s: s[1])[0]
        alpha = _alpha_for(det)
        kind = det["kind"]
        print(f"detected: {kind}  "
              f"box={det['x']},{det['y']} {det['width']}x{det['height']}  "
              f"margin={det.get('margin_right')}/{det.get('margin_bottom')}  "
              f"score={det.get('confidence', 0):.3f}")
        if kind == "text":
            print(f"  template={det['template']}")

    pos = {"x": det["x"], "y": det["y"], "width": det["width"], "height": det["height"]}

    if args.detect_only:
        return 0

    if args.gain is not None:
        gain, residual = args.gain, float("nan")
        print(f"gain  : {gain:.4f} (forced)")
    else:
        seed = _seed_for(det) if det["kind"] != "rect" else 1.0
        gain, info = estimate_gain(frames, pos, alpha, seed)
        if info["frames"] == 0:
            print("Không ước lượng được gain", file=sys.stderr)
            return 3
        print(f"gain  : {gain:.4f}  (seed {seed:.3f}, "
              f"{info['used']}/{info['frames']} frame phẳng, "
              f"spread {info['spread']:.3f})")
        if info["used"] == 0:
            print("  cảnh báo: không frame nền phẳng nào cắt 0 -> kiểm tra bằng --gain")
        if info["boundary"]:
            print("  cảnh báo: gain chạm biên -> kiểm tra bằng --gain")
        if info["spread"] > 1.0:
            print("  cảnh báo: gain giữa các frame lệch nhiều -> kiểm tra bằng --gain")

    A = build_alpha(alpha, gain, pos["height"], pos["width"])
    A_full = np.zeros((meta["height"], meta["width"]), dtype=np.float32)
    A_full[pos["y"]:pos["y"] + pos["height"], pos["x"]:pos["x"] + pos["width"]] = A
    AC = chroma_alpha(A_full)
    cbox = chroma_box(pos)
    y_logo = 255.0 if meta.get("color_range") == "pc" else 235.0
    uv_logo = 128.0

    dst = Path(args.out) if args.out else OUT_DIR / f"{src.stem}_clean.mp4"
    crf = 0 if args.lossless else args.crf
    print(f"encode: CRF {crf}, preset {args.preset}, audio copy -> {dst}")

    cmd = encode(src, dst, meta, crf, args.preset)
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    total = 0
    try:
        for Y, U, V in frame_iter_yuv(src, meta["width"], meta["height"]):
            restore_yuv(Y, U, V, pos, A, AC, cbox, y_logo, uv_logo)
            enc.stdin.write(Y.tobytes())
            enc.stdin.write(U.tobytes())
            enc.stdin.write(V.tobytes())
            total += 1
            if total % 120 == 0:
                print(f"  ... {total} frame", flush=True)
    finally:
        enc.stdin.close()
        rc = enc.wait()
    if rc != 0:
        print(f"ffmpeg encode lỗi (rc={rc})", file=sys.stderr)
        return 4

    out_size = dst.stat().st_size
    src_size = src.stat().st_size
    print(f"OK    : {dst}  ({out_size / 1e6:.2f} MB, nguồn {src_size / 1e6:.2f} MB, {total} frame)")

    if args.verify:
        print("verify:")
        verify(src, dst, meta, pos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
