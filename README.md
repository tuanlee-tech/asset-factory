# asset-factory

Web app local cho pipeline sản xuất video: **xóa watermark**, **TTS tiếng Việt**,
**dựng video Remotion** — 100% chạy trên máy, không upload cloud.

```
app.py + templates/ + static/      web UI dark SaaS (Watermark + TTS + Studio)
tts_engine.py                      TTS + SRT (vietneu mặc định → zero → edge fallback)
video_dewm.py                      xóa watermark video (reverse alpha blending)
image_dewm.py                      xóa watermark ảnh (diamond inpaint)
make_promo.py                      pipeline promo tự động (screenshot + voice + SFX + ffmpeg)
remotion/                          Remotion project (composition FactoryCut, Studio :3000)
tools/video/                       alpha map trích từ repo MIT GargantuaX (commit sẵn)
.agents/skills/                    agent skills: remotion-best-practices/create/render, …
HANDOFF.md                         bàn giao chi tiết + bẫy đã gặp → ĐỌC TRƯỚC KHI SỬA
```

## Cài đặt & chạy

```bash
pip install -r requirements.txt
python app.py                              # http://127.0.0.1:5000
cd remotion && npm i && npx remotion studio --port 3000   # nền song song
```

Ubuntu 22.04+: `bash setup_ubuntu.sh` (1 lần) rồi `bash start.sh` (Flask + Studio).

Biến môi trường: `HOST`, `PORT`, `PUBLIC_BASE` (URL gốc khi mở UI từ máy khác).

Yêu cầu: Python 3.12+, ffmpeg full build (`libx264`), Node 22+.

---

## Web UI (`http://127.0.0.1:5000`)

Theme dark SaaS (Linear/Vercel-style). Nguồn vào luôn do người dùng chọn.

### Tab Watermark
- **Ảnh**: dropzone multi-file, chế độ Đơn (so sánh trước/sau slider) hoặc Hàng loạt.
- **Video**: job nền + log realtime, tùy chỉnh `--rect/--gain/CRF`.folders
- Xử lý 100% local.

### Tab TTS
- **Provider mặc định: VieNeu** (chạy máy, 25 giọng Bắc/Trung/Nam).
- **Tốc độ mặc định +50%** — áp dụng mọi provider (vietneu/zero qua ffmpeg atempo).
- Fallback tự động: `vietneu → zero → edge` (edge chỉ dùng khi local fail).
- Giọng mặc định: **Hải Đăng**. Nút **Nghe thử giọng** (câu mẫu ~2s).
- Text dài tự chunk + tổng hợp song song, xuất MP3/WAV kèm `.srt`.

```bash
# CLI
python tts_engine.py "Xin chào thế giới."                    # vietneu Hải Đăng +50%
python tts_engine.py --provider edge --rate +10% "..."
python tts_engine.py --list-voices --provider vietneu
python tts_speak.py "..."                                     # legacy, edge-only
```

### Tab Studio (Remotion)
- Iframe Remotion Studio (`:3000/FactoryCut`) — sửa composition trực quan.
- Card **Render MP4**: upload nhiều clip (video/ảnh) + audio nền →
  `POST /api/studio/render` → job Remotion → preview + tải `assets/video/*.mp4`.
- 1920×1080 · 30fps.

### API
`POST /api/upload` · `POST /api/watermark/image` · `POST /api/watermark/video` ·
`GET /api/jobs/<id>` · `GET /api/tts/voices?provider=` · `POST /api/tts` ·
`POST /api/tts/preview` · `POST /api/studio/render` · `GET /f/<kind>/<name>`.

---

## Promo video tự động — `make_promo.py`

```bash
python app.py          # cần nền
python make_promo.py   # ~1–2 phút → assets/video/assetsfactory_promo.mp4
```

Pipeline: screenshot 3 tab (Edge headless) → voice Hải Đăng +50% → nhạc pad +
SFX (whoosh/impact/riser, synth numpy) → scene encode (Ken Burns push/pan/pull,
lower-third, vignette+grain) → xfade transitions + sidechain ducking + loudnorm.

---

## Xóa watermark video — `video_dewm.py`

Nguyên lý reverse alpha blending (không AI inpaint — inpainting vẽ lại nội dung
là mất chi tiết):

```
original = (observed − a·LOGO) / (1 − a)
```

- Chỉ sửa ROI vài chục px; audio `copy` bit-identical; video encode 1 lần (CRF 14).
- PSNR ngoài ROI **bằng đúng** một lần re-encode thuần (thực tế 45.19 dB).
- Nguồn thuật toán: [`GargantuaX/gemini-watermark-remover`](https://github.com/GargantuaX/gemini-watermark-remover) (MIT).

```bash
python video_dewm.py input.mp4                    # auto detect + ước alpha
python video_dewm.py input.mp4 --detect-only      # chỉ xem vùng detect
python video_dewm.py input.mp4 --rect 682,1254,23,10 --gain 2.8
python video_dewm.py input.mp4 --lossless         # CRF 0, ngoài ROI tuyệt đối
python video_dewm.py input.mp4 --verify           # PSNR + watermark score
```

| Option | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `-o` | `assets/video/<tên>_clean.mp4` | output |
| `--crf` | `14` | x264 CRF |
| `--lossless` | off | CRF 0 |
| `--rect x,y,w,h` | auto | chỉ định tay nếu auto sai |
| `--gain` | auto | alpha gain |
| `--verify` | off | đo trước/sau |

Watermark detect: **diamond** (Gemini/Veo logo) ✅ · **text** `Veo` 3 size ✅ ·
**SynID** ❌ (ẩn, không xóa được nếu không mất chất lượng).

Ảnh tĩnh: `python image_dewm.py input.png` (diamond, so sánh trước/sau).

---

## Trích lại alpha map (nâng cao)

`tools/video/data/*.bin` đã commit. Chỉ chạy lại khi cập nhật từ repo nguồn:

```bash
git clone https://github.com/GargantuaX/gemini-watermark-remover
node tools/video/extract_alpha_maps.mjs ./gemini-watermark-remover
```

---

## Agent skills

```
npx skills add remotion-dev/skills@remotion-best-practices -y
npx skills add remotion-dev/skills@remotion-create -y
npx skills add remotion-dev/skills@remotion-render -y
npx skills add heygen-com/hyperframes@hyperframes-animation -y
npx skills add leonxlnx/taste-skill@high-end-visual-design -y
```

Chi tiết bàn giao: **[HANDOFF.md](HANDOFF.md)**.
