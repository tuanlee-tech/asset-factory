"use strict";
const $ = (id) => document.getElementById(id);

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.hidden = true; }, 3500);
}
function busy(btn, on) {
  if (!btn) return;
  btn.disabled = !!on;
  btn.classList.toggle("loading", !!on);
}
function fmtSize(n) {
  if (n > 1e6) return (n / 1e6).toFixed(2) + " MB";
  if (n > 1e3) return (n / 1e3).toFixed(1) + " KB";
  return n + " B";
}
async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
async function uploadFile(file, target) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("target", target);
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

// ---------- save-as (File System Access API, fallback = download thường) ----------
const HAS_FSA = typeof window.showSaveFilePicker === "function";
async function saveAsFile(url, name) {
  if (HAS_FSA) {
    try {
      const handle = await window.showSaveFilePicker({ suggestedName: name });
      const resp = await fetch(url);
      if (!resp.ok) throw new Error("Không tải được " + name);
      const w = await handle.createWritable();
      await w.write(await resp.blob());
      await w.close();
      toast("Đã lưu: " + handle.name);
      return;
    } catch (e) {
      if (e && e.name === "AbortError") return; // user huỷ picker
      // fallthrough -> download thường
    }
  }
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
}
async function saveToDir(files) {
  if (!window.showDirectoryPicker) {
    toast("Trình duyệt không hỗ trợ chọn thư mục — dùng nút Tải (.zip) thay.");
    return;
  }
  const dir = await window.showDirectoryPicker({ mode: "readwrite" });
  let n = 0;
  for (const f of files) {
    try {
      const resp = await fetch(f.url);
      if (!resp.ok) continue;
      const fh = await dir.getFileHandle(f.name, { create: true });
      const w = await fh.createWritable();
      await w.write(await resp.blob());
      await w.close();
      n++;
    } catch (e) { console.warn("saveToDir", f.name, e); }
  }
  toast("Đã lưu " + n + "/" + files.length + " file vào thư mục đã chọn.");
}
async function downloadZip(items, zipname) {
  if (!items.length) { toast("Không có file nào được chọn."); return; }
  const r = await fetch("/api/download/zip", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, zipname }) });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error || ("HTTP " + r.status));
  }
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = zipname || "assets-factory.zip";
  a.click();
  URL.revokeObjectURL(a.href);
}

// tabs
document.querySelectorAll(".tab").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    ["watermark", "tts", "studio"].forEach((t) => {
      $("tab-" + t).hidden = (t !== b.dataset.tab);
    });
  });
});
(function initDeepLink() {
  const t = new URLSearchParams(location.search).get("tab");
  const btn = t && document.querySelector('.tab[data-tab="' + t + '"]');
  if (btn) btn.click();
})();

// ---------- steps ----------
function setStep(n) {
  for (let k = 1; k <= 3; k++) {
    const li = $("st" + k);
    if (!li) continue;
    li.classList.toggle("done", k < n);
    li.classList.toggle("active", k === n);
  }
}
let IMODE = "single";

// ---------- dropzone ----------
function wireDrop(dropId, inputId) {
  const dz = $(dropId), inp = $(inputId);
  if (!dz || !inp) return;
  dz.addEventListener("click", () => inp.click());
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inp.click(); }
  });
  ["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => {
    e.preventDefault(); dz.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => {
    e.preventDefault(); dz.classList.remove("over");
  }));
  dz.addEventListener("drop", (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      inp.files = e.dataTransfer.files;
      inp.dispatchEvent(new Event("change"));
    }
  });
}

// ---------- watermark video (nguồn: file người dùng chọn) ----------
let vTimer = null;
let VFILE = null;
let VOUT = null;
function vOpts(detectOnly) {
  const gain = $("v-gain").value.trim();
  return {
    crf: parseInt($("v-crf").value || "14", 10),
    preset: $("v-preset").value,
    rect: $("v-rect").value.trim() || null,
    gain: gain === "" ? null : parseFloat(gain),
    lossless: $("v-lossless").checked,
    verify: $("v-verify").checked,
    detect_only: detectOnly };
}
async function vRun(detectOnly) {
  if (!VFILE) { toast("Chọn video trước đã."); return; }
  $("v-status").className = "status";
  $("v-status").textContent = detectOnly ? "Đang detect..." : "Đang tải lên + xử lý (có thể mất vài phút)...";
  $("v-log").hidden = false;
  $("v-log").textContent = "";
  $("v-result").hidden = true;
  busy($("v-detect"), true); busy($("v-clean"), true);
  clearInterval(vTimer);
  try {
    const up = await uploadFile(VFILE, "uploads");
    const body = vOpts(detectOnly);
    body.kind = up.kind; body.name = up.name;
    const { job_id } = await postJSON("/api/watermark/video", body);
    vTimer = setInterval(async () => {
      try {
        const j = await getJSON("/api/jobs/" + job_id);
        $("v-log").textContent = j.log || "";
        $("v-log").scrollTop = 1e9;
        if (j.status === "done") {
          clearInterval(vTimer);
          busy($("v-detect"), false); busy($("v-clean"), false);
          if (detectOnly) {
            $("v-status").className = "status ok";
            $("v-status").textContent = "Detect xong (xem log).";
            setStep(2);
          } else if (j.result) {
            $("v-status").className = "status ok";
            $("v-status").textContent = "OK -> " + j.result.name;
            $("v-out").src = j.result.url;
            $("v-dl").href = j.result.url;
            VOUT = j.result;
            $("v-result").hidden = false;
            setStep(3);
          } else {
            $("v-status").className = "status ok";
            $("v-status").textContent = "Xong (không có file output).";
            setStep(3);
          }
        } else if (j.status === "error") {
          clearInterval(vTimer);
          busy($("v-detect"), false); busy($("v-clean"), false);
          $("v-status").className = "status err";
          $("v-status").textContent = j.error || "Lỗi (xem log).";
        }
      } catch (e) {
        clearInterval(vTimer);
        busy($("v-detect"), false); busy($("v-clean"), false);
        $("v-status").className = "status err";
        $("v-status").textContent = e.message;
      }
    }, 2000);
  } catch (e) {
    busy($("v-detect"), false); busy($("v-clean"), false);
    $("v-status").className = "status err";
    $("v-status").textContent = e.message;
  }
}
$("v-detect").addEventListener("click", () => vRun(true));
$("v-clean").addEventListener("click", () => vRun(false));
$("v-saveas").addEventListener("click", () => {
  if (VOUT) saveAsFile(VOUT.url, VOUT.name);
});
wireDrop("v-drop", "v-upload");
$("v-upload").addEventListener("change", () => {
  if ($("v-upload").files.length) {
    VFILE = $("v-upload").files[0];
    $("v-sel").src = URL.createObjectURL(VFILE);
    $("v-selname").textContent = VFILE.name + " (" + fmtSize(VFILE.size) + ")";
    $("v-selwrap").hidden = false;
    $("v-result").hidden = true;
    setStep(2);
  }
});

// ---------- watermark image ----------
function iParams() {
  return {
    thresh: parseFloat($("i-thresh").value || "12"),
    pad: parseInt($("i-pad").value || "4", 10),
    rect: $("i-rect").value.trim() || null,
    quality: parseInt($("i-quality").value || "100", 10) };
}
let IFILES = [];
async function iRun() {
  if (!IFILES.length) { toast("Chọn ảnh trước đã."); return; }
  $("i-status").className = "status";
  $("i-status").textContent = "Đang xử lý...";
  $("i-result").hidden = true; $("i-dl").hidden = true; $("i-saveas").hidden = true;
  $("i-bulkgrid").hidden = true; $("i-bulkgrid").innerHTML = "";
  $("i-bulkbar").hidden = true;
  IBULKOK = [];
  $("i-info").textContent = "";
  busy($("i-clean"), true);
  if (IMODE === "bulk" && IFILES.length > 1) {
    await iBulk(IFILES);
    busy($("i-clean"), false);
    return;
  }
  try {
    const up = await uploadFile(IFILES[0], "uploads");
    const p = iParams();
    const j = await postJSON("/api/watermark/image", {
      kind: up.kind, name: up.name,
      thresh: p.thresh, pad: p.pad, rect: p.rect, quality: p.quality });
    $("i-before").src = j.src.url;
    $("i-after").src = j.out.url + "?t=" + Date.now();
    $("i-dl").href = j.out.url;
    ISINGLE = j.out;
    $("i-result").hidden = false; $("i-dl").hidden = false; $("i-saveas").hidden = false;
    const b = j.bbox;
    $("i-info").textContent = "bbox=" + b.x + "," + b.y + " " + b.width + "x" + b.height +
      " (" + b.pixels + " px) -> " + j.out.name;
    $("i-status").className = "status ok";
    $("i-status").textContent = "OK";
    setStep(3);
  } catch (e) {
    $("i-status").className = "status err";
    $("i-status").textContent = e.message;
  }
  busy($("i-clean"), false);
}
let IBULKOK = [];   // [{kind, name, url}]
let ISINGLE = null;
async function iBulk(files) {
  const grid = $("i-bulkgrid");
  grid.innerHTML = ""; grid.hidden = false;
  let ok = 0;
  for (let idx = 0; idx < files.length; idx++) {
    $("i-status").className = "status";
    $("i-status").textContent = "Đang xử lý " + (idx + 1) + "/" + files.length + "...";
    try {
      const up = await uploadFile(files[idx], "uploads");
      const p = iParams();
      const j = await postJSON("/api/watermark/image", {
        kind: up.kind, name: up.name,
        thresh: p.thresh, pad: p.pad, rect: p.rect, quality: p.quality });
      addBulkItem(j.out.url, j.out.name, "", j.out.kind || "visual");
      ok++;
    } catch (e) { addBulkItem("", files[idx].name, e.message); }
  }
  setStep(3);
  $("i-status").className = "status ok";
  $("i-status").textContent = "Xong " + ok + "/" + files.length + " ảnh.";
  $("i-bulkbar").hidden = false;
  syncSelAll();
}
function addBulkItem(url, name, err, kind) {
  const d = document.createElement("div");
  d.className = "bulkitem";
  if (url) {
    const bi = IBULKOK.length;
    IBULKOK.push({ kind: kind || "visual", name: name, url: url });
    d.dataset.bi = String(bi);
    const cbWrap = document.createElement("label");
    cbWrap.className = "bulkchk";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = true; cb.className = "i-chk";
    cb.addEventListener("change", syncSelAll);
    cbWrap.appendChild(cb);
    d.appendChild(cbWrap);
    const t = document.createElement("img");
    t.loading = "lazy"; t.alt = ""; t.src = url;
    d.appendChild(t);
  }
  const n = document.createElement("div");
  n.className = "nm"; n.textContent = name;
  d.appendChild(n);
  const w = document.createElement("div");
  const a = document.createElement("a");
  if (url) { a.href = url; a.download = name; a.textContent = "Tải về"; }
  else { a.textContent = err || "Lỗi"; a.className = "err"; }
  w.appendChild(a); d.appendChild(w);
  $("i-bulkgrid").appendChild(d);
}
function syncSelAll() {
  const boxes = $("i-bulkgrid").querySelectorAll("input.i-chk");
  if (!boxes.length) return;
  const all = [...boxes].every((b) => b.checked);
  $("i-selall").checked = all;
}
$("i-selall").addEventListener("change", () => {
  $("i-bulkgrid").querySelectorAll("input.i-chk").forEach((b) => { b.checked = $("i-selall").checked; });
});
function selectedBulk() {
  return [...$("i-bulkgrid").querySelectorAll(".bulkitem")].flatMap((el) => {
    const cb = el.querySelector("input.i-chk");
    if (!cb || !cb.checked) return [];
    const item = IBULKOK[+el.dataset.bi];
    return item ? [item] : [];
  });
}
$("i-dl-all").addEventListener("click", async () => {
  if (!IBULKOK.length) { toast("Chưa có ảnh kết quả."); return; }
  busy($("i-dl-all"), true);
  try { await downloadZip(IBULKOK, "watermark-sach.zip"); }
  catch (e) { toast(e.message); }
  busy($("i-dl-all"), false);
});
$("i-dl-sel").addEventListener("click", async () => {
  const items = selectedBulk();
  if (!items.length) { toast("Chưa chọn ảnh nào."); return; }
  busy($("i-dl-sel"), true);
  try { await downloadZip(items, "watermark-chon.zip"); }
  catch (e) { toast(e.message); }
  busy($("i-dl-sel"), false);
});
$("i-dl-dir").addEventListener("click", async () => {
  let items = IBULKOK;
  const boxes = $("i-bulkgrid").querySelectorAll("input.i-chk");
  if (boxes.length) {
    const anyChecked = [...boxes].some((b) => b.checked);
    if (anyChecked) items = selectedBulk();
  }
  if (!items.length) { toast("Chưa có ảnh kết quả."); return; }
  busy($("i-dl-dir"), true);
  try { await saveToDir(items); }
  catch (e) { toast(e.message || String(e)); }
  busy($("i-dl-dir"), false);
});
$("i-saveas").addEventListener("click", () => {
  if (ISINGLE) saveAsFile(ISINGLE.url, ISINGLE.name);
});
$("i-clean").addEventListener("click", iRun);
wireDrop("i-drop", "i-upload");
$("i-upload").addEventListener("change", () => {
  IFILES = [...$("i-upload").files];
  if (IFILES.length) {
    const single = IFILES.length === 1;
    $("i-sel").hidden = !single;
    if (single) {
      $("i-sel").src = URL.createObjectURL(IFILES[0]);
      $("i-selgrid").hidden = true;
      $("i-selgrid").innerHTML = "";
    } else {
      const g = $("i-selgrid");
      g.hidden = false;
      g.innerHTML = "";
      IFILES.forEach((f) => {
        const d = document.createElement("div");
        d.className = "bulkitem";
        const t = document.createElement("img");
        t.loading = "lazy"; t.alt = "";
        t.src = URL.createObjectURL(f);
        d.appendChild(t);
        const n = document.createElement("div");
        n.className = "nm"; n.textContent = f.name;
        d.appendChild(n);
        g.appendChild(d);
      });
    }
    $("i-selname").textContent = IFILES.length > 1 ?
      (IFILES.length + " ảnh: " + IFILES.map((f) => f.name).join(", ")) :
      (IFILES[0].name + " (" + fmtSize(IFILES[0].size) + ")");
    $("i-selwrap").hidden = false;
    $("i-result").hidden = true; $("i-dl").hidden = true; $("i-saveas").hidden = true;
    $("i-bulkgrid").hidden = true; $("i-bulkgrid").innerHTML = "";
    $("i-bulkbar").hidden = true;
    IBULKOK = []; ISINGLE = null;
    setStep(2);
  }
});

// ---------- seg + mode ----------
$("seg-image").addEventListener("click", () => {
  $("seg-image").classList.add("active"); $("seg-video").classList.remove("active");
  $("seg-image").setAttribute("aria-selected", "true"); $("seg-video").setAttribute("aria-selected", "false");
  $("ws-image").hidden = false; $("ws-video").hidden = true; setStep(1);
});
$("seg-video").addEventListener("click", () => {
  $("seg-video").classList.add("active"); $("seg-image").classList.remove("active");
  $("seg-video").setAttribute("aria-selected", "true"); $("seg-image").setAttribute("aria-selected", "false");
  $("ws-video").hidden = false; $("ws-image").hidden = true; setStep(1);
});
$("m-single").addEventListener("click", () => {
  IMODE = "single";
  $("m-single").classList.add("active"); $("m-bulk").classList.remove("active");
});
$("m-bulk").addEventListener("click", () => {
  IMODE = "bulk";
  $("m-bulk").classList.add("active"); $("m-single").classList.remove("active");
});

// ---------- TTS ----------
function fmtPct(v, unit) {
  const n = parseInt(v || "0", 10);
  return (n > 0 ? "+" : "") + n + unit;
}
function syncRangeLabels() {
  $("t-rate-val").textContent = fmtPct($("t-rate").value, "%");
  $("t-pitch-val").textContent = fmtPct($("t-pitch").value, "Hz");
  for (const id of ["t-rate", "t-pitch"]) {
    const el = $(id);
    const min = parseFloat(el.min), max = parseFloat(el.max), v = parseFloat(el.value);
    el.style.setProperty("--range-pct", ((v - min) / (max - min) * 100).toFixed(2) + "%");
  }
}
$("t-rate").addEventListener("input", syncRangeLabels);
$("t-pitch").addEventListener("input", syncRangeLabels);
syncRangeLabels();

async function loadVoices() {
  const p = $("t-provider").value;
  const sel = $("t-voice");
  sel.innerHTML = "";
  const o0 = document.createElement("option");
  o0.textContent = "đang tải voices…";
  sel.appendChild(o0);
  const ctl = new AbortController();
  const to = setTimeout(() => ctl.abort(), 120000);
  try {
    const r = await fetch("/api/tts/voices?provider=" + encodeURIComponent(p), { signal: ctl.signal });
    const vs = await r.json().catch(() => ([]));
    if (!r.ok) throw new Error(vs.error || ("HTTP " + r.status));
    sel.innerHTML = "";
    if (!vs.length) throw new Error("provider này chưa có voice");
    vs.forEach((v) => {
      const o = document.createElement("option");
      o.value = v.id;
      o.textContent = v.label;
      sel.appendChild(o);
    });
    // mặc định giọng Hải Đăng nếu có
    const hd = Array.from(sel.options).find((o) => o.value === "Hải Đăng");
    if (hd) sel.value = "Hải Đăng";
  } catch (e) {
    sel.innerHTML = "";
    const o = document.createElement("option");
    o.textContent = (e.name === "AbortError" ? "quá thời gian (model đang nạp) - bấm ↻ thử lại" : "lỗi: " + e.message + " - bấm ↻ thử lại");
    o.disabled = true;
    sel.appendChild(o);
  } finally {
    clearTimeout(to);
  }
}
$("t-provider").addEventListener("change", () => { stopPreview(); loadVoices(); });
$("t-vreload").addEventListener("click", loadVoices);

// ---------- nghe thử giọng (câu mẫu cố định, không cần gõ) ----------
let previewAudio = null;
function stopPreview() {
  if (previewAudio) { previewAudio.pause(); previewAudio = null; }
}
$("t-preview").addEventListener("click", async () => {
  const btn = $("t-preview");
  stopPreview();
  busy(btn, true);
  $("t-status").className = "status";
  $("t-status").textContent = "Đang tạo giọng mẫu...";
  try {
    const j = await postJSON("/api/tts/preview", {
      provider: $("t-provider").value,
      voice: $("t-voice").value || null,
      rate: fmtPct($("t-rate").value, "%"),
      pitch: fmtPct($("t-pitch").value, "Hz"),
    });
    previewAudio = new Audio(j.audio.url);
    previewAudio.addEventListener("ended", () => {
      $("t-status").className = "status";
      $("t-status").textContent = "";
    });
    await previewAudio.play();
    $("t-status").className = "status ok";
    $("t-status").textContent = "Đang phát mẫu " + j.durationSec + "s — rate/pitch áp dụng ngay.";
  } catch (e) {
    $("t-status").className = "status err";
    $("t-status").textContent = e.message;
  } finally {
    busy(btn, false);
  }
});
$("t-text").addEventListener("input", () => {
  $("t-count").textContent = $("t-text").value.length + " ký tự";
});
let tTimer = null;
let TAUDIO = null;
let TSRT = null;
$("t-saveas-a").addEventListener("click", () => {
  if (TAUDIO) saveAsFile(TAUDIO.url, TAUDIO.name);
});
$("t-saveas-s").addEventListener("click", () => {
  if (TSRT) saveAsFile(TSRT.url, TSRT.name);
});
$("t-speak").addEventListener("click", async () => {
  const text = $("t-text").value.trim();
  if (!text) { toast("Nhập nội dung trước đã."); return; }
  $("t-status").className = "status";
  $("t-status").textContent = "Đang tổng hợp (chunk song song)...";
  $("t-log").hidden = false;
  $("t-log").textContent = "";
  $("t-result").hidden = true;
  busy($("t-speak"), true);
  clearInterval(tTimer);
  try {
    const { job_id } = await postJSON("/api/tts", {
      text, provider: $("t-provider").value,
      voice: $("t-voice").value || null,
      rate: fmtPct($("t-rate").value, "%"), pitch: fmtPct($("t-pitch").value, "Hz"),
      format: $("t-format").value, workers: 4 });
    tTimer = setInterval(async () => {
      try {
        const j = await getJSON("/api/jobs/" + job_id);
        $("t-log").textContent = j.log || "";
        if (j.status === "done") {
          clearInterval(tTimer);
          busy($("t-speak"), false);
          $("t-status").className = "status ok";
          $("t-status").textContent = "OK - " + j.result.durationSec + "s, " + j.result.chunks + " chunks";
          $("t-out").src = j.result.audio.url;
          $("t-dla").href = j.result.audio.url;
          $("t-dls").href = j.result.srt.url;
          TAUDIO = j.result.audio; TSRT = j.result.srt;
          $("t-result").hidden = false;
        } else if (j.status === "error") {
          clearInterval(tTimer);
          busy($("t-speak"), false);
          $("t-status").className = "status err";
          $("t-status").textContent = j.error || "Lỗi.";
        }
      } catch (e) {
        clearInterval(tTimer);
        busy($("t-speak"), false);
        $("t-status").className = "status err";
        $("t-status").textContent = e.message;
      }
    }, 2000);
  } catch (e) {
    busy($("t-speak"), false);
    $("t-status").className = "status err";
    $("t-status").textContent = e.message;
  }
});

// ---------- Studio (Remotion render) ----------
let SCLIPS = [];
let SAUDIO = null;
function renderClips() {
  const box = $("s-clips");
  box.innerHTML = "";
  if (!SCLIPS.length) {
    const es = document.createElement("div");
    es.className = "emptystate";
    es.textContent = "Chưa có clip nào — chọn video hoặc ảnh ở trên.";
    box.appendChild(es);
    return;
  }
  SCLIPS.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "cliprow";
    const idx = document.createElement("span");
    idx.className = "clipidx";
    idx.textContent = String(i + 1).padStart(2, "0");
    const meta = document.createElement("div");
    meta.className = "clipmeta";
    const nm = document.createElement("b");
    nm.textContent = c.file.name;
    const sub = document.createElement("span");
    sub.textContent = String(c.kind || "").toUpperCase() +
      (c.meta && c.meta.durationSec ? " · " + c.meta.durationSec + "s" : "") +
      " · " + fmtSize(c.file.size);
    meta.appendChild(nm); meta.appendChild(sub);
    const dur = document.createElement("input");
    dur.type = "number"; dur.className = "clipdur";
    dur.min = "0.5"; dur.step = "0.5"; dur.value = c.durationSec;
    dur.title = "Thời lượng (giây)";
    dur.addEventListener("change", () => { c.durationSec = parseFloat(dur.value) || c.durationSec; });
    const rm = document.createElement("button");
    rm.className = "iconbtn rm"; rm.type = "button"; rm.title = "Xóa clip";
    rm.innerHTML = "&#10005;";
    rm.addEventListener("click", () => { SCLIPS.splice(i, 1); renderClips(); });
    row.appendChild(idx); row.appendChild(meta); row.appendChild(dur); row.appendChild(rm);
    box.appendChild(row);
  });
}
renderClips();
$("s-upload").addEventListener("change", async () => {
  for (const f of $("s-upload").files) {
    let meta = {};
    try { meta = await uploadFile(f, "uploads"); } catch (e) { toast(e.message); continue; }
    const isVideo = /\.(mp4|mov|mkv|webm)$/i.test(f.name);
    SCLIPS.push({
      file: f, kind: meta.kind, name: meta.name, meta: meta.meta || {},
      durationSec: (meta.meta && meta.meta.durationSec) || (isVideo ? 5 : 3) });
  }
  $("s-upload").value = "";
  renderClips();
});
$("s-aud-upload").addEventListener("change", async () => {
  const f = $("s-aud-upload").files[0];
  if (!f) return;
  try {
    SAUDIO = await uploadFile(f, "uploads");
    $("s-aud-name").textContent = SAUDIO.name;
  } catch (e) { toast(e.message); }
  $("s-aud-upload").value = "";
});
let sTimer = null;
let SOUT = null;
$("s-saveas").addEventListener("click", () => {
  if (SOUT) saveAsFile(SOUT.url, SOUT.name);
});
$("s-render").addEventListener("click", async () => {
  if (!SCLIPS.length) { toast("Thêm clip trước đã."); return; }
  $("s-status").className = "status";
  $("s-status").textContent = "Đang render bằng Remotion (có thể mất vài phút)...";
  $("s-log").hidden = false;
  $("s-log").textContent = "";
  $("s-result").hidden = true;
  busy($("s-render"), true);
  clearInterval(sTimer);
  try {
    const { job_id } = await postJSON("/api/studio/render", {
      outName: ($("s-outname").value || "factorycut").trim() || "factorycut",
      bg: $("s-bg").value || "#101828",
      clips: SCLIPS.map((c) => ({ kind: c.kind, name: c.name, durationSec: c.durationSec })),
      audio: SAUDIO ? { kind: SAUDIO.kind, name: SAUDIO.name } : null });
    sTimer = setInterval(async () => {
      try {
        const j = await getJSON("/api/jobs/" + job_id);
        $("s-log").textContent = j.log || "";
        $("s-log").scrollTop = 1e9;
        if (j.status === "done") {
          clearInterval(sTimer);
          busy($("s-render"), false);
          $("s-status").className = "status ok";
          $("s-status").textContent = "OK -> " + j.result.name;
          $("s-out").src = j.result.url;
          $("s-dl").href = j.result.url;
          SOUT = j.result;
          $("s-result").hidden = false;
        } else if (j.status === "error") {
          clearInterval(sTimer);
          busy($("s-render"), false);
          $("s-status").className = "status err";
          $("s-status").textContent = j.error || "Lỗi (xem log).";
        }
      } catch (e) {
        clearInterval(sTimer);
        busy($("s-render"), false);
        $("s-status").className = "status err";
        $("s-status").textContent = e.message;
      }
    }, 3000);
  } catch (e) {
    busy($("s-render"), false);
    $("s-status").className = "status err";
    $("s-status").textContent = e.message;
  }
});

// ---------- health ----------
async function health() {
  try {
    const j = await getJSON("/api/health");
    if (j.ok) {
      $("srv-dot").classList.add("on");
      $("srv-txt").textContent = "máy chủ đang chạy";
      return;
    }
  } catch (e) { /* offline */ }
  $("srv-dot").classList.remove("on");
  $("srv-txt").textContent = "mất kết nối";
}
health();
setInterval(health, 15000);

loadVoices();