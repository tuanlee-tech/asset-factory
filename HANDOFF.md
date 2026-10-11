# HANDOFF — Assets Factory

Cập nhật: 2026-10-11. Repo: `tuanlee-tech/asset-factory` (nhánh `main`).

## 1. Dự án là gì

Web app local (Flask :5000 + Remotion Studio :3000) cho pipeline sản xuất video:

- **Watermark** — xóa watermark ảnh (inpaint diamond) + video (reverse alpha blending, không AI inpaint).
- **TTS tiếng Việt** — 3 provider: `vietneu` (mặc định, chạy máy), `zero` (offline), `edge` (fallback cuối). Chunk song song, xuất MP3/WAV + SRT.
- **Studio** — nhúng Remotion Studio, composition `FactoryCut` (ghép clip + audio nền), render MP4 từ UI.
- **make_promo.py** — pipeline 1 lệnh: screenshot UI thật → voice Hải Đăng → nhạc + SFX synth → Ken Burns/xfade/ducking → `assets/video/assetsfactory_promo.mp4`.

## 2. Chạy nhanh

```bash
pip install -r requirements.txt
python app.py                    # http://127.0.0.1:5000
cd remotion && npm i && npx remotion studio --port 3000   # nền thứ 2 (hoặc bash start.sh trên Ubuntu)
python make_promo.py             # cần Flask đang chạy + ffmpeg full build
```

Ubuntu: `bash setup_ubuntu.sh` rồi `bash start.sh`.

Biến môi trường: `HOST`, `PORT`, `PUBLIC_BASE` (URL gốc để Remotion tải clip khi truy cập từ máy khác).

## 3. Trạng thái đã hoàn thành

| Hạng mục | Trạng thái |
| --- | --- |
| Web UI dark SaaS (Linear/Vercel-style) | ✅ viết lại hoàn toàn `templates/index.html` + `static/style.css` |
| TTS default: VietNeu "Hải Đăng" @ +50% | ✅ |
| Fallback chain `vietneu → zero → edge` | ✅ `tts_engine.PROVIDER_PRIORITY`, test giả lập fail OK |
| Rate +50% mọi provider (atempo post-synth) | ✅ `_apply_rate_atempo` |
| Nghe thử giọng (`POST /api/tts/preview`) | ✅ |
| CSS range slider filled/unfilled | ✅ `--range-pct` (WebKit) + `::-moz-range-progress` (FF) |
| Watermark video `video_dewm.py` | ✅ PSNR ngoài ROI = re-encode thuần |
| Watermark ảnh `image_dewm.py` | ✅ |
| Studio render qua UI | ✅ `POST /api/studio/render` |
| Promo video 20s chuyên nghiệp | ✅ camera push/pan/pull, lower-third, xfade, SFX, sidechain duck |
| Skill Remotion (official) + animation + design | ✅ trong `.agents/skills/` (xem §6) |

## 4. Bẫy đã gặp — đọc trước khi sửa

1. **UTF-8 Windows console**: mọi script Python mới cần `sys.stdout.reconfigure(encoding="utf-8")` ở đầu file — in tiếng Việt otherwise crash `cp1252`.
2. **ffmpeg drawtext**: dùng `expansion=none` + option `alpha='expr'` (KHÔNG dùng `fontcolor=white@expr` — không animate được). Ký tự `%` trong text phải escape hoặc nhờ `expansion=none`.
3. **Edge headless screenshot**: `--timeout=12000` ổn định; TRÁNH `--virtual-time-budget` (treo vì animation CSS infinite). Server Flask phải đang chạy — nếu không Edge exit 0 mà không ghi PNG.
4. **Index filter ffmpeg**: input thứ tự = thứ tự `-i`. Audio nằm sau video → `[5:a] [6:a] [7:a]` chứ không phải `[2:a]`… (bug đã fix trong make_promo.py).
5. **Dropdown dark**: `select option { background:#14141a; color:… }` — Windows render option trắng otherwise.
6. **JS contract**: mọi `$("id")` trong `static/app.js` phải tồn tại trong `templates/index.html`. Classes `.tab/.active/.status.ok.err/.bulkitem/.prow/.loading` do JS tạo — CSS phải match.
7. **Provider voice ID có dấu**: VietNeu dùng đúng `"Hải Đăng"` (có dấu). `"Hai Dang"` fail.
8. **Deep-link**: `?tab=watermark|tts|studio` — dùng cho screenshot headless; thêm tab mới thì cập nhật `initDeepLink` trong app.js.

## 5. Quy ước code

- **Không overwrite UI từ đầu** — người dùng đã yêu cầu 2 lần; sửa incrementally, giữ ID/contract.
- UI tiếng Việt có dấu; file UTF-8 không BOM.
- Card system: `.card > .chead(.cicon/.ctitle/.csub/.chead-actions) + .cbody(.fsection/.fslabel) + .cfoot(.foot-note)` + `.status` + `.log` + `.resultpanel`.
- Primary button đen mực (không gradient); secondary `.btn.ghost`; luôn có spinner `.loading` khi async (`busy(btn, on)`).
- API pattern: `POST …` trả `{job_id}` → poll `GET /api/jobs/<id>` → `status: running|done|error` + `log` + `result`.
- Output để trong `assets/audio/`, `assets/video/`; static serve qua `GET /f/<kind>/<name>`.

## 6. Skill đã cài (`.agents/skills/` + `skills-lock.json`)

```
remotion-best-practices   remotion-dev (official) — patterns, perf
remotion-create           remotion-dev — scaffold composition
remotion-render           remotion-dev — CLI render/props
hyperframes-animation     HeyGen — spring/stagger/easing pro
high-end-visual-design    leonxlnx — taste/typography
```

Cài lại: `npx skills add <owner/repo@skill> -y` (global `-g` không hỗ trợ môi trường này).

Khi sửa `remotion/src/*.tsx`: load skill `remotion-best-practices` trước, rồi `remotion-create`.

## 7. File map

```
app.py                      Flask API + static + jobs + preload VietNeu
tts_engine.py               synthesize_all + list_voices + fallback chain
tts_speak.py                CLI TTS legacy (giữ tương thích)
image_dewm.py               xóa watermark ảnh (diamond)
video_dewm.py               xóa watermark video (reverse alpha)
make_promo.py               pipeline promo video tự động
templates/index.html        3 tab: watermark / tts / studio
static/style.css            design system dark SaaS
static/app.js               fetch API, tabs, jobs poll, preview TTS
remotion/src/FactoryCut.tsx composition ghép clip + audio
remotion/src/Root.tsx       đăng ký composition (defaultProps clips rỗng)
setup_ubuntu.sh / start.sh  bootstrap + run Ubuntu
tools/video/                alpha map extract (MIT GargantuaX)
.agents/skills/             agent skills (xem §6)
promo/                      working dir make_promo (gitignored)
```

## 8. Next steps gợi ý

1. **Nâng cấp `FactoryCut.tsx`**: thêm transition, title card, lower-third (dùng skill remotion-best-practices + hyperframes-animation). Hiện composition chỉ `Series` ghép thô.
2. **Watermark residue cleanup**: port `videoCleanupBackends` từ repo GargantuaX (gradient weight map, bilateral) để hết vệt mờ sau inpaint.
3. **Promo v2**: nếu muốn dài hơn thì bỏ `+50%` rate trong `make_promo.SCENES` hoặc viết kịch bản dài; có thể render bằng Remotion thay ffmpeg để chuyển cảnh mượt hơn.
4. **Preview TTS cache**: `POST /api/tts/preview` sinh lại mỗi lần — thêm LRU cache theo `(provider, voice, rate, pitch)`.
5. **PUBLIC_BASE**: khi demo từ máy khác, set env trước khi start Remotion, nếu không iframe Studio không load clip được.

## 9. Verify nhanh sau khi pull

```bash
node --check static/app.js
python -c "import ast; [ast.parse(open(f,encoding='utf-8').read()) for f in ['app.py','tts_engine.py','make_promo.py']]"
curl -s http://127.0.0.1:5000/api/health
curl -s "http://127.0.0.1:5000/api/tts/voices?provider=vietneu" | head -c 200
```
