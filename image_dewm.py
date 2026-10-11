#!/usr/bin/env python3
"""Xóa watermark diamond + text (Gemini/Veo) khỏi ẢNH tĩnh — reverse alpha blending.

Nguyên lý GIỐNG video_dewm.py: watermark là alpha blend của logo trắng lên
nền, nên đảo được một cách toán học:

    observed = a*LOGO + (1-a)*original
    original = (observed - a*LOGO) / (1-a)

Dùng lại detector + alpha map từ video_dewm (diamond NCC + text NCC).
Clamp alpha theo nền cục bộ để không kéo sáng giả pixel nền.

Usage:
  python image_dewm.py input.jpg
  python image_dewm.py input.jpg -o out.jpg
  python image_dewm.py input.jpg --detect-only
  python image_dewm.py input.jpg --rect 1255,647,48,48
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("Thiếu Pillow: pip install pillow", file=sys.stderr)
    raise

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent

LOGO = 255.0
ALPHA_MIN = 0.025
CLAMP_TAU = 0.5
DEFAULT_THRESH = 12.0
DEFAULT_QUALITY = 100

# import detector + alpha helpers từ video_dewm (cùng repo)
sys.path.insert(0, str(ROOT))
from video_dewm import (  # noqa: E402
    _alpha_for, _seed_for, detect, text_templates, video_alpha_map,
)
from collections import deque  # noqa: E402


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im.convert("RGB"))


# -------- fallback detection (color-diff, không cần NCC) --------

def tl_background(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    hh, ww = min(100, h), min(100, w)
    patch = img[10:10 + hh, 10:10 + ww].astype(float).reshape(-1, 3)
    return np.median(patch, axis=0)


def _largest_component(mask: np.ndarray):
    H, W = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    best_count = 0
    best_box = None
    for yy in range(H):
        for xx in range(W):
            if mask[yy, xx] and not visited[yy, xx]:
                q = deque()
                q.append((yy, xx))
                visited[yy, xx] = True
                count = 0
                x0 = x1 = xx
                y0 = y1 = yy
                while q:
                    cy, cx = q.popleft()
                    count += 1
                    if cx < x0: x0 = cx
                    if cx > x1: x1 = cx
                    if cy < y0: y0 = cy
                    if cy > y1: y1 = cy
                    for dy in (-1, 0, 1):
                        ny = cy + dy
                        if ny < 0 or ny >= H: continue
                        for dx in (-1, 0, 1):
                            nx = cx + dx
                            if nx < 0 or nx >= W: continue
                            if mask[ny, nx] and not visited[ny, nx]:
                                visited[ny, nx] = True
                                q.append((ny, nx))
                if count > best_count:
                    best_count = count
                    best_box = (x0, x1, y0, y1)
    if best_box is None:
        return None
    return best_count, best_box


def _find_color_bbox(img: np.ndarray, search: int = 160, thresh: float = DEFAULT_THRESH) -> dict | None:
    h, w = img.shape[:2]
    bg = tl_background(img)
    s = max(48, min(search, w, h))
    x0, y0 = w - s, h - s
    roi = img[y0:h, x0:w].astype(float)
    diff = np.sqrt(((roi - bg) ** 2).sum(axis=2))
    for t in (thresh, thresh * 0.65, thresh * 0.4, 4.0):
        mask = diff > t
        if not bool(mask.any()):
            continue
        found = _largest_component(mask)
        if found is None:
            continue
        count, (lx0, lx1, ly0, ly1) = found
        min_px = max(40, int(100 * t / DEFAULT_THRESH))
        if count < min_px:
            continue
        gx0, gx1 = x0 + lx0, x0 + lx1
        gy0, gy1 = y0 + ly0, y0 + ly1
        return {
            "x": int(gx0), "y": int(gy0),
            "width": int(gx1 - gx0 + 1), "height": int(gy1 - gy0 + 1),
            "kind": "diamond",  # fallback luôn coi như diamond
            "size": int(max(gx1 - gx0 + 1, gy1 - gy0 + 1)),
            "pixels": int(count),
        }
    return None


def detect_watermark(img: np.ndarray) -> dict | None:
    """Detector từ video_dewm (diamond + text NCC), thêm scaled text + fallback."""
    pos = detect(img)
    if pos is not None:
        return pos

    # scaled text detection (video templates là size 1920x1080, ảnh có thể nhỏ hơn)
    h, w = img.shape[:2]
    scale = min(w / 1920.0, h / 1080.0)
    if scale < 0.99:
        best_t, best_ncc = None, -1.0
        templates = text_templates()
        from video_dewm import resize_map, rect_ncc_grid, gray_region
        # quét rộng: nửa dưới-phải ảnh
        x_lo0, y_lo0 = max(0, w // 2), max(0, h // 2)
        region = gray_region(img, x_lo0, y_lo0, w - x_lo0, h - y_lo0)
        for sc in (scale * 0.55, scale * 0.65, scale * 0.75, scale * 0.85, scale):
            for tid, t in templates.items():
                tw = max(8, int(round(t["width"] * sc)))
                th = max(4, int(round(t["height"] * sc)))
                if tw > region.shape[1] or th > region.shape[0]:
                    continue
                det = t["detector"]
                oh, ow = det.shape
                det_s = resize_map(det.reshape(-1).astype(np.float32), ow, oh, tw, th).reshape(th, tw)
                grid = rect_ncc_grid(region, det_s)
                if grid.size == 0:
                    continue
                idx = np.unravel_index(int(np.argmax(grid)), grid.shape)
                val = float(grid[idx])
                if val > best_ncc:
                    best_ncc = val
                    best_t = {
                        **t, "kind": "text", "template": tid,
                        "width": tw, "height": th,
                        "x": x_lo0 + int(idx[1]), "y": y_lo0 + int(idx[0]),
                        "spatial": val, "confidence": max(0.0, val),
                        "detector_scaled": det_s,
                    }
        if best_t and best_ncc >= 0.45:  # thấp hơn video (0.62) vì ảnh JPEG nén
            return best_t

    # fallback: color-diff diamond
    fb = _find_color_bbox(img)
    if fb is not None:
        # ensure square for diamond alpha
        side = max(fb["width"], fb["height"])
        fb["width"] = fb["height"] = side
        fb["size"] = side
        fb.setdefault("margin_right", 0)
        fb.setdefault("margin_bottom", 0)
        fb.setdefault("inset", False)
        # pre-compute alpha map cho diamond
        fb["alpha"] = video_alpha_map(side, fb)
        return fb
    return None


def _plane_bg(luma: np.ndarray, x: int, y: int, bw: int, bh: int, pad: int) -> float:
    h, w = luma.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    ring = luma[y0:y1, x0:x1]
    mask = np.ones(ring.shape, dtype=bool)
    ry, rx = y - y0, x - x0
    if 0 <= ry < ring.shape[0] and 0 <= rx < ring.shape[1]:
        mask[ry:ry + bh, rx:rx + bw] = False
    sub = ring[mask]
    if sub.size < 4:
        return float("nan")
    return float(np.median(sub))


def clean_array(img_arr: np.ndarray, pos: dict) -> tuple[np.ndarray, dict]:
    """Precise mask + inpaint — chỉ inpaint pixels watermark thực sự.

    Watermark trên ảnh AI-generated là bright overlay (không phải white alpha
    blend). Reverse-alpha làm nó tối hơn; inpaint cả bbox làm mất texture.
    Strategy:
      1. Alpha map từ video → shape guide (star/diamond outline)
      2. Brightness excess = observed - bg_luma
      3. Precise mask = (excess > thresh) AND (alpha > 0.1) — chỉ pixels
         thực sự là watermark
      4. Inpaint TELEA trên precise mask
      5. Feathered blend để tránh artifact

    pos: dict từ detect_watermark() (kind diamond/text, x, y, width, height, alpha)
    """
    import cv2

    h, w = img_arr.shape[:2]
    x, y, bw, bh = pos["x"], pos["y"], pos["width"], pos["height"]

    # Alpha map cho shape guide
    if pos.get("kind") == "text" and "detector_scaled" in pos:
        A = pos["detector_scaled"].astype(np.float32).reshape(bh, bw)
    else:
        A = _alpha_for(pos).reshape(bh, bw).astype(np.float32)
    A = np.clip(A, 0.0, 1.0)

    # Region with pad for context
    pad = max(16, bw // 8)
    ry0, ry1 = max(0, y - pad), min(h, y + bh + pad)
    rx0, rx1 = max(0, x - pad), min(w, x + bw + pad)
    region = img_arr[ry0:ry1, rx0:rx1].copy()
    gray_full = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY).astype(np.float32)

    # Inner bbox (offset by pad)
    iy0, iy1 = y - ry0, y - ry0 + bh
    ix0, ix1 = x - rx0, x - rx0 + bw
    inner = gray_full[iy0:iy1, ix0:ix1]

    # Background luma from ring (outside bbox)
    ring_mask = np.ones(gray_full.shape, dtype=bool)
    ring_mask[iy0:iy1, ix0:ix1] = False
    bg_luma = float(np.median(gray_full[ring_mask])) if ring_mask.any() else float(np.median(inner))

    # Brightness excess (watermark brighter than bg)
    excess = np.clip(inner - bg_luma, 0, None)

    # Adaptive threshold based on bg brightness
    if bg_luma < 100:
        bright_thresh = 8.0
    elif bg_luma < 180:
        bright_thresh = 5.0
    else:
        bright_thresh = 3.0

    # Watermark mask: alpha shape (primary) + brightness (secondary boost)
    # Alpha shape captures the full diamond/star outline
    # Brightness adds strength where watermark is clearly visible
    alpha_mask = (A > 0.08).astype(np.uint8)  # low threshold to catch faint edges
    bright_mask = (excess > bright_thresh).astype(np.uint8)

    # Union: alpha shape OR bright pixels (within bbox)
    combined_mask = cv2.bitwise_or(alpha_mask, bright_mask)

    # Morphological close to fill gaps in watermark shape
    combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    # Dilate slightly to cover edges
    mask_inner = cv2.dilate(combined_mask, np.ones((3, 3), np.uint8), iterations=1) * 255

    # Pad mask to region
    mask = np.zeros(gray_full.shape, dtype=np.uint8)
    mask[iy0:iy1, ix0:ix1] = mask_inner

    mask_px = int((mask > 0).sum())
    if mask_px < 10:
        info = {
            "fill_rect": [x, y, bw, bh],
            "mask_px": 0,
            "method": "precise-skip",
            "bg_luma": bg_luma,
        }
        return img_arr.copy(), info

    # Inpaint ONLY masked pixels
    inpainted = cv2.inpaint(region, mask, inpaintRadius=5, flags=cv2.INPAINT_TELEA)

    # Feathered blend
    blend = (mask > 0).astype(np.float32)
    blend = cv2.GaussianBlur(blend, (15, 15), 5)
    blend = np.clip(blend * 2.0, 0, 1)

    out_region = region.astype(np.float32) * (1 - blend[..., None]) + inpainted.astype(np.float32) * blend[..., None]
    out_region = np.clip(np.rint(out_region), 0, 255).astype(np.uint8)

    out = img_arr.copy()
    out[ry0:ry1, rx0:rx1] = out_region

    info = {
        "fill_rect": [x, y, bw, bh],
        "mask_px": mask_px,
        "method": "precise-inpaint-" + pos.get("kind", "?"),
        "bg_luma": bg_luma,
    }
    return out, info


def default_out(src: Path) -> Path:
    return src.parent / (src.stem + "_clean" + src.suffix)


def clean_file(src: Path, dst: Path | None = None, thresh: float = DEFAULT_THRESH,
               rect: str | None = None, quality: int = DEFAULT_QUALITY) -> dict:
    img_arr = load_rgb(src)
    h, w = img_arr.shape[:2]
    if rect:
        x, y, bw, bh = (int(v) for v in rect.split(","))
        pos = {"kind": "rect", "x": x, "y": y, "width": bw, "height": bh}
        auto = False
    else:
        pos = detect_watermark(img_arr)
        auto = True
        if pos is None:
            raise ValueError("Không phát hiện watermark. Dùng --rect x,y,w,h nếu bạn chắc chắn nó có.")
    out_arr, fill_info = clean_array(img_arr, pos)
    dst = dst or default_out(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    pil = Image.fromarray(out_arr)
    if dst.suffix.lower() in (".jpg", ".jpeg"):
        pil.save(dst, quality=quality, subsampling=0)
    else:
        pil.save(dst)
    return {"src": str(src), "dst": str(dst), "auto": auto,
            "bbox": {"x": pos["x"], "y": pos["y"],
                     "width": pos["width"], "height": pos["height"],
                     "pixels": pos["width"] * pos["height"]},
            **fill_info}


def main() -> int:
    p = argparse.ArgumentParser(description="Xóa watermark (reverse alpha, diamond + text)")
    p.add_argument("input")
    p.add_argument("-o", "--out")
    p.add_argument("--thresh", type=float, default=DEFAULT_THRESH)
    p.add_argument("--rect", help="x,y,w,h")
    p.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    p.add_argument("--detect-only", action="store_true")
    args = p.parse_args()

    src = Path(args.input)
    if not src.exists():
        print("Không thấy file: " + str(src), file=sys.stderr)
        return 1
    img_arr = load_rgb(src)
    h, w = img_arr.shape[:2]
    print("input : " + str(src) + "  " + str(w) + "x" + str(h))
    if args.rect:
        x, y, bw, bh = (int(v) for v in args.rect.split(","))
        print("forced rect: " + str(x) + "," + str(y) + "," + str(bw) + "," + str(bh))
        if args.detect_only:
            return 0
    else:
        pos = detect_watermark(img_arr)
        if pos is None:
            print("Không phát hiện watermark. Dùng --rect x,y,w,h nếu bạn chắc chắn nó có.")
            return 2
        print("detected: " + pos["kind"] + " box=" + str(pos["x"]) + "," + str(pos["y"]) +
              " " + str(pos["width"]) + "x" + str(pos["height"]))
        if args.detect_only:
            return 0
    info = clean_file(src, Path(args.out) if args.out else None,
                      thresh=args.thresh, rect=args.rect, quality=args.quality)
    print("OK    : " + info["dst"] + "  method=" + info["method"] +
          "  mask_px=" + str(info["mask_px"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
