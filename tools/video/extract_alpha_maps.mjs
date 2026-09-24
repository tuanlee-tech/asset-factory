#!/usr/bin/env node
/**
 * Trích xuất alpha map đã hiệu chuẩn từ repo gốc (MIT) sang binary asset.
 * Chạy 1 lần để sinh tools/video/data/alpha_<key>.bin
 *
 *   node tools/video/extract_alpha_maps.mjs <path-to-gemini-watermark-remover>
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const KEYS = ['96-20260520', '96', '48', '36-v2'];

const srcRepo = resolve(process.argv[2] ?? '.');
const outDir = join(dirname(fileURLToPath(import.meta.url)), 'data');
mkdirSync(outDir, { recursive: true });

const mod = await import(
  pathToFileURL(join(srcRepo, 'src/core/embeddedAlphaMaps.js')).href
);

for (const key of KEYS) {
  const map = mod.getEmbeddedAlphaMap(key);
  if (!map) {
    console.warn(`skip ${key}: not found`);
    continue;
  }
  const buf = Buffer.from(map.buffer, map.byteOffset, map.byteLength);
  writeFileSync(join(outDir, `alpha_${key}.bin`), buf);
  console.log(`alpha_${key}.bin  ${map.length} float32  ${buf.length} bytes`);
}

const textMod = await import(
  pathToFileURL(join(srcRepo, 'src/video/veoTextWatermarkTemplates.js')).href
);

const meta = {};
for (const id of textMod.VEO_TEXT_TEMPLATE_IDS) {
  const m = textMod.getVeoTextTemplateMetadata(id);
  const det = textMod.getVeoTextTemplateDetectorMap(id);
  writeFileSync(join(outDir, `veotext_${id}.bin`), Buffer.from(det.buffer, det.byteOffset, det.byteLength));
  meta[id] = {
    width: m.width,
    height: m.height,
    margin_right: m.marginRight,
    margin_bottom: m.marginBottom,
    min_ncc: m.minNcc,
    observed_seed_scale: m.observedSeedScale,
    ...(m.allenkObservedRegion ? { observed_region: m.allenkObservedRegion } : {})
  };
  console.log(`veotext_${id}.bin  ${m.width}x${m.height}  ${det.length} float32`);
}
writeFileSync(join(outDir, 'templates.json'), JSON.stringify(meta, null, 2));
console.log('templates.json written');
