#!/usr/bin/env python3
"""Xóa watermark diamond (Gemini/Veo) khỏi ẢNH tĩnh, giữ nguyên chất lượng.

Nguyên lý: watermark nằm trên nền xanh phẳng (flat) nên detect bằng
khoảng cách màu RGB so với nền cục bộ, giữ connected-component lớn nhất
(watermark ~800-900 px, nhiễu JPEG chỉ 1-2 px rời rạc), rồi fill hình chữ
nhật mở rộng bằng median nền + noise Gauss theo std nền.

Chi dung numpy + Pillow (khong can OpenCV).

Usage:
  python image_dewm.py input.jpg
  python image_dewm.py input.jpg -o out.jpg
  python image_dewm.py input.jpg --detect-only
  python image_dewm.py input.jpg --rect 1255,647,48,48
"""
from __future__ import annotations

import argparse
import sys
from collections import deque
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

DEFAULT_THRESH = 12.0
DEFAULT_SEARCH = 160
DEFAULT_PAD = 4
DEFAULT_QUALITY = 100


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im.convert("RGB"))


def tl_background(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    hh, ww = min(100, h), min(100, w)
    patch = img[10:10 + hh, 10:10 + ww].astype(float).reshape(-1, 3)
    return np.median(patch, axis=0)


def _largest_component(mask: np.ndarray):
    """Tìm connected-component (8-hướng) lớn nhất. Trả về (count, (x0,x1,y0,y1)) hoặc None."""
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
                    if cx < x0:
                        x0 = cx
                    if cx > x1:
                        x1 = cx
                    if cy < y0:
                        y0 = cy
                    if cy > y1:
                        y1 = cy
                    for dy in (-1, 0, 1):
                        ny = cy + dy
                        if ny < 0 or ny >= H:
                            continue
                        for dx in (-1, 0, 1):
                            nx = cx + dx
                            if nx < 0 or nx >= W:
                                continue
                            if mask[ny, nx] and not visited[ny, nx]:
                                visited[ny, nx] = True
                                q.append((ny, nx))
                if count > best_count:
                    best_count = count
                    best_box = (x0, x1, y0, y1)
    if best_box is None:
        return None
    return best_count, best_box


def find_diamond_bbox(img: np.ndarray, search: int = DEFAULT_SEARCH,
                      thresh: float = DEFAULT_THRESH) -> dict | None:
    """Tìm bbox watermark diamond trong ảnh. Trả về dict hoặc None."""
    h, w = img.shape[:2]
    bg = tl_background(img)
    s = max(48, min(search, w, h))
    x0, y0 = w - s, h - s
    roi = img[y0:h, x0:w].astype(float)
    diff = np.sqrt(((roi - bg) ** 2).sum(axis=2))
    mask = diff > thresh
    if not bool(mask.any()):
        return None
    found = _largest_component(mask)
    if found is None:
        return None
    count, (lx0, lx1, ly0, ly1) = found
    # Loc nhieu JPEG: watermark that phai co kich thuoc dang ke
    if count < 100:
        return None
    gx0, gx1 = x0 + lx0, x0 + lx1
    gy0, gy1 = y0 + ly0, y0 + ly1
    return {
        "x": int(gx0), "y": int(gy0),
        "width": int(gx1 - gx0 + 1), "height": int(gy1 - gy0 + 1),
        "pixels": int(count), "bg": [float(v) for v in bg],
    }


def clean_array(img_arr: np.ndarray, bbox: dict, pad: int = DEFAULT_PAD,
                seed: int = 0) -> tuple[np.ndarray, dict]:
    """Fill vùng bbox (+pad) bằng median nền cục bộ + noise. Trả về (out, info)."""
    h, w = img_arr.shape[:2]
    img = img_arr.astype(float)
    gx0, gy0, bw, bh = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
    gx1, gy1 = gx0 + bw - 1, gy0 + bh - 1

    ring_pad = 12
    rx0, ry0 = max(0, gx0 - ring_pad), max(0, gy0 - ring_pad)
    rx1, ry1 = min(w, gx1 + 1 + ring_pad), min(h, gy1 + 1 + ring_pad)
    ring = img[ry0:ry1, rx0:rx1]
    inner = np.zeros(ring.shape[:2], dtype=bool)
    inner[gy0 - ry0:gy1 + 1 - ry0, gx0 - rx0:gx1 + 1 - rx0] = True
    outer = ring[~inner]
    local_med = np.median(outer, axis=0)
    local_std = outer.std(axis=0)

    fx0, fy0 = max(0, gx0 - pad), max(0, gy0 - pad)
    fx1, fy1 = min(w, gx1 + 1 + pad), min(h, gy1 + 1 + pad)
    fh, fw = fy1 - fy0, fx1 - fx0
    rng = np.random.default_rng(seed)
    fill = np.tile(local_med, (fh, fw, 1))
    fill = fill + rng.normal(0, 1, (fh, fw, 3)) * local_std
    fill = np.clip(np.rint(fill), 0, 255).astype(np.uint8)

    out = img_arr.copy()
    out[fy0:fy1, fx0:fx1] = fill
    info = {
        "fill_rect": [fx0, fy0, fw, fh],
        "local_median": [float(v) for v in local_med],
        "local_std": [float(v) for v in local_std],
    }
    return out, info


def default_out(src: Path) -> Path:
    return src.parent / (src.stem + "_clean" + src.suffix)


def clean_file(src: Path, dst: Path | None = None, thresh: float = DEFAULT_THRESH,
               pad: int = DEFAULT_PAD, rect: str | None = None,
               quality: int = DEFAULT_QUALITY, seed: int = 0) -> dict:
    """Xoa watermark 1 file anh. Tra ve info dict."""
    img_arr = load_rgb(src)
    if rect:
        x, y, bw, bh = (int(v) for v in rect.split(","))
        bbox = {"x": x, "y": y, "width": bw, "height": bh,
                "pixels": bw * bh, "bg": [float(v) for v in tl_background(img_arr)]}
        auto = False
    else:
        bbox = find_diamond_bbox(img_arr, thresh=thresh)
        auto = True
        if bbox is None:
            raise ValueError("Không phát hiện watermark. Dùng --rect x,y,w,h nếu bạn chắc chắn nó có.")
    out_arr, fill_info = clean_array(img_arr, bbox, pad=pad, seed=seed)
    dst = dst or default_out(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    pil = Image.fromarray(out_arr)
    if dst.suffix.lower() in (".jpg", ".jpeg"):
        pil.save(dst, quality=quality, subsampling=0)
    else:
        pil.save(dst)
    return {"src": str(src), "dst": str(dst), "auto": auto,
            "bbox": bbox, **fill_info}


def main() -> int:
    p = argparse.ArgumentParser(description="Xóa watermark diamond khỏi ảnh tĩnh")
    p.add_argument("input")
    p.add_argument("-o", "--out")
    p.add_argument("--thresh", type=float, default=DEFAULT_THRESH)
    p.add_argument("--pad", type=int, default=DEFAULT_PAD)
    p.add_argument("--rect", help="Bắt buộc vùng watermark: x,y,w,h")
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
        bbox = find_diamond_bbox(img_arr, thresh=args.thresh)
        if bbox is None:
            print("Không phát hiện watermark. Dùng --rect x,y,w,h nếu bạn chắc chắn nó có.")
            return 2
        print("detected: diamond box=" + str(bbox["x"]) + "," + str(bbox["y"]) +
              " " + str(bbox["width"]) + "x" + str(bbox["height"]) +
              " pixels=" + str(bbox["pixels"]))
        if args.detect_only:
            return 0
    info = clean_file(src, Path(args.out) if args.out else None,
                      thresh=args.thresh, pad=args.pad, rect=args.rect,
                      quality=args.quality)
    print("OK    : " + info["dst"] + "  fill=" + str(info["fill_rect"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
