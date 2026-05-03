#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const rel = (...p) => path.join(root, ...p);
const exists = (p) => fs.existsSync(rel(p));
const readJson = (p) => JSON.parse(fs.readFileSync(rel(p), 'utf8'));
const listJson = (dir) => exists(dir) ? fs.readdirSync(rel(dir)).filter((f) => f.endsWith('.json')).sort() : [];
const listFiles = (dir) => exists(dir) ? fs.readdirSync(rel(dir)).sort() : [];
const fileStem = (f) => f.replace(/\.[^.]+$/, '');

const args = new Set(process.argv.slice(2));
const jsonMode = args.has('--json');

const errors = [];
const warnings = [];

function issue(level, code, message, details = {}) {
  const item = { code, message, ...details };
  if (level === 'error') errors.push(item);
  else warnings.push(item);
}

function safeJson(p) {
  try {
    return readJson(p);
  } catch (error) {
    issue('error', 'invalid_json', `Invalid JSON: ${p}`, { file: p, error: error.message });
    return null;
  }
}

const shardFiles = listJson('data/clinics');
let clinics = [];
let shardStats = [];

for (const f of shardFiles) {
  const p = `data/clinics/${f}`;
  const arr = safeJson(p);
  if (!Array.isArray(arr)) {
    issue('error', 'invalid_shard_shape', `Clinic shard is not an array: ${p}`, { file: p });
    continue;
  }
  shardStats.push({ file: f, records: arr.length });
  clinics.push(...arr.map((c) => ({ ...c, _shard: f })));
}

const countBy = (key) => clinics.reduce((m, c) => {
  const value = c[key] ?? '(missing)';
  m[value] = (m[value] || 0) + 1;
  return m;
}, {});

const placeCounts = new Map();
for (const c of clinics) {
  if (!c.placeId) continue;
  placeCounts.set(c.placeId, (placeCounts.get(c.placeId) || 0) + 1);
}
const duplicatePlaceIds = [...placeCounts]
  .filter(([, count]) => count > 1)
  .map(([placeId, count]) => ({ placeId, count }));

if (duplicatePlaceIds.length) {
  issue('error', 'duplicate_place_id', `${duplicatePlaceIds.length} duplicate placeId value(s) found`, {
    samples: duplicatePlaceIds.slice(0, 20),
  });
}

const requiredFields = ['placeId', 'slug', 'name', 'classification', 'lastSeenAt'];
const missingFields = Object.fromEntries(requiredFields.map((field) => [field, clinics.filter((c) => !c[field]).length]));
for (const [field, count] of Object.entries(missingFields)) {
  if (count) issue('error', 'missing_required_field', `${count} records missing ${field}`, { field, count });
}

const routeMissing = {
  citySlug: clinics.filter((c) => !c.citySlug).length,
  stateSlug: clinics.filter((c) => !c.stateSlug).length,
};

const directoryClasses = new Set(['primary_trt', 'offers_trt']);
function isPermanentlyClosed(c) {
  return c.businessStatus === 'CLOSED_PERMANENTLY' || c.closed === true || c.permanentlyClosed === true;
}
function isGhost(c) {
  if (isPermanentlyClosed(c)) return false;
  return !c.phone && !c.rating && !(Array.isArray(c.hours) && c.hours.length > 0);
}

const eligible = clinics.filter((c) => directoryClasses.has(c.classification) && !isGhost(c));
const liveDirectory = eligible.filter((c) => !c.telehealth && !isPermanentlyClosed(c) && c.stateSlug && c.citySlug && c.slug);
const eligibleMissingRoute = eligible.filter((c) => !c.telehealth && !isPermanentlyClosed(c) && (!c.stateSlug || !c.citySlug || !c.slug));
if (eligibleMissingRoute.length) {
  issue('error', 'eligible_missing_route', `${eligibleMissingRoute.length} public directory records are missing route fields`, {
    samples: eligibleMissingRoute.slice(0, 20).map((c) => ({ placeId: c.placeId, name: c.name, state: c.state, stateSlug: c.stateSlug, city: c.city, citySlug: c.citySlug })),
  });
}

const shardStateMismatches = clinics
  .filter((c) => c.stateSlug && c._shard !== `${c.stateSlug}.json`)
  .map((c) => ({ placeId: c.placeId, name: c.name, shard: c._shard, stateSlug: c.stateSlug }));
if (shardStateMismatches.length) {
  issue('error', 'shard_state_mismatch', `${shardStateMismatches.length} records live in a shard that does not match stateSlug`, {
    samples: shardStateMismatches.slice(0, 20),
  });
}

const unknownRecords = clinics.filter((c) => c._shard === '_unknown.json' || !c.stateSlug);
if (unknownRecords.length) {
  issue('warning', 'unknown_shard_records', `${unknownRecords.length} records remain in _unknown or lack stateSlug`, {
    samples: unknownRecords.slice(0, 20).map((c) => ({ placeId: c.placeId, name: c.name, state: c.state, stateSlug: c.stateSlug, classification: c.classification })),
  });
}

const pageFiles = listJson('data/clinic_pages');
const writeupFiles = listJson('data/clinic_writeups');
const imageFiles = listFiles('public/img/clinics').filter((f) => /\.(png|jpg|jpeg|webp|svg)$/i.test(f));
const clinicIds = new Set(clinics.map((c) => c.placeId).filter(Boolean));
const eligibleIds = new Set(eligible.map((c) => c.placeId).filter(Boolean));
const pageIds = new Set(pageFiles.map(fileStem));
const writeupIds = new Set(writeupFiles.map(fileStem));
const imageIds = new Set(imageFiles.map(fileStem));

const orphanPages = pageFiles.map(fileStem).filter((id) => !clinicIds.has(id));
const orphanWriteups = writeupFiles.map(fileStem).filter((id) => !clinicIds.has(id));
const orphanImages = imageFiles.map(fileStem).filter((id) => !clinicIds.has(id));
for (const [label, values] of [['orphan_pages', orphanPages], ['orphan_writeups', orphanWriteups], ['orphan_images', orphanImages]]) {
  if (values.length) issue('warning', label, `${values.length} ${label.replace('_', ' ')} found`, { samples: values.slice(0, 20) });
}

const eligibleMissingPages = [...eligibleIds].filter((id) => !pageIds.has(id));
const eligibleMissingWriteups = [...eligibleIds].filter((id) => !writeupIds.has(id));
const eligibleMissingLocalIcons = [...eligibleIds].filter((id) => !imageIds.has(id));
const eligibleWithPhotoGallery = eligible.filter((c) => Array.isArray(c.photos) && c.photos.length > 0).length;
const eligibleMissingPhotoGallery = eligible.filter((c) => !(Array.isArray(c.photos) && c.photos.length > 0)).length;
const eligibleMissingAnyVisual = eligible
  .filter((c) => !imageIds.has(c.placeId) && !(Array.isArray(c.photos) && c.photos.length > 0))
  .map((c) => c.placeId);
if (eligibleMissingPages.length) issue('warning', 'eligible_missing_pages', `${eligibleMissingPages.length} eligible records missing fetched page artifact`, { samples: eligibleMissingPages.slice(0, 20) });
if (eligibleMissingWriteups.length) issue('warning', 'eligible_missing_writeups', `${eligibleMissingWriteups.length} eligible records missing writeup artifact`, { samples: eligibleMissingWriteups.slice(0, 20) });
if (eligibleMissingLocalIcons.length) issue('warning', 'eligible_missing_local_icons', `${eligibleMissingLocalIcons.length} eligible records missing local card icon asset`, { samples: eligibleMissingLocalIcons.slice(0, 20) });
if (eligibleMissingAnyVisual.length) issue('warning', 'eligible_missing_any_visual', `${eligibleMissingAnyVisual.length} eligible records missing both local card icon and Google photo gallery data`, { samples: eligibleMissingAnyVisual.slice(0, 20) });

const telehealth = {};
for (const p of ['data/telehealth.json', 'data/telehealth-seed.json', 'data/telehealth-seed-validated.json']) {
  if (!exists(p)) continue;
  const data = safeJson(p);
  telehealth[p] = Array.isArray(data) ? data.length : (data && typeof data === 'object' ? Object.keys(data).length : null);
}

const report = {
  generatedAt: new Date().toISOString(),
  root,
  ok: errors.length === 0,
  summary: {
    shardFiles: shardFiles.length,
    clinicRecords: clinics.length,
    eligibleRecords: eligible.length,
    liveDirectoryRecords: liveDirectory.length,
    unknownRecords: unknownRecords.length,
    duplicatePlaceIds: duplicatePlaceIds.length,
    errors: errors.length,
    warnings: warnings.length,
    clinicPages: pageFiles.length,
    clinicWriteups: writeupFiles.length,
    clinicImages: imageFiles.length,
  },
  distributions: {
    classification: countBy('classification'),
    classificationConfidence: countBy('classificationConfidence'),
    businessStatus: countBy('businessStatus'),
    topShards: shardStats.sort((a, b) => b.records - a.records).slice(0, 15),
  },
  missingFields: { ...missingFields, ...routeMissing },
  coverage: {
    eligibleWithPage: [...eligibleIds].filter((id) => pageIds.has(id)).length,
    eligibleMissingPages: eligibleMissingPages.length,
    eligibleWithWriteup: [...eligibleIds].filter((id) => writeupIds.has(id)).length,
    eligibleMissingWriteups: eligibleMissingWriteups.length,
    eligibleWithLocalIcon: [...eligibleIds].filter((id) => imageIds.has(id)).length,
    eligibleMissingLocalIcons: eligibleMissingLocalIcons.length,
    eligibleWithPhotoGallery,
    eligibleMissingPhotoGallery,
    eligibleWithAnyVisual: eligible.length - eligibleMissingAnyVisual.length,
    eligibleMissingAnyVisual: eligibleMissingAnyVisual.length,
    orphanPages: orphanPages.length,
    orphanWriteups: orphanWriteups.length,
    orphanImages: orphanImages.length,
  },
  telehealth,
  errors,
  warnings,
};

if (jsonMode) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`TRT Index data validation: ${report.ok ? 'PASS' : 'FAIL'}`);
  console.log(`- clinic records: ${report.summary.clinicRecords.toLocaleString()} across ${report.summary.shardFiles} shards`);
  console.log(`- eligible/live: ${report.summary.eligibleRecords.toLocaleString()} / ${report.summary.liveDirectoryRecords.toLocaleString()}`);
  console.log(`- duplicate placeIds: ${report.summary.duplicatePlaceIds}`);
  console.log(`- unknown records: ${report.summary.unknownRecords}`);
  console.log(`- coverage gaps: pages=${report.coverage.eligibleMissingPages}, writeups=${report.coverage.eligibleMissingWriteups}, localIcons=${report.coverage.eligibleMissingLocalIcons}, anyVisual=${report.coverage.eligibleMissingAnyVisual}`);
  console.log(`- warnings: ${warnings.length}; errors: ${errors.length}`);
  for (const err of errors) console.error(`ERROR ${err.code}: ${err.message}`);
  for (const warn of warnings.slice(0, 8)) console.warn(`WARN ${warn.code}: ${warn.message}`);
  if (warnings.length > 8) console.warn(`WARN: ${warnings.length - 8} additional warning(s); rerun with --json for details.`);
}

process.exit(errors.length ? 1 : 0);
