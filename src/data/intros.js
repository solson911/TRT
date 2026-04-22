// Loads generated per-state/per-city SEO intros from data/page-intros.json.
// Returns null for pages without an intro so callers can skip rendering.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INTROS_PATH = path.join(__dirname, '..', '..', 'data', 'page-intros.json');

let cached = null;

function load() {
  if (cached) return cached;
  if (!fs.existsSync(INTROS_PATH)) {
    cached = { states: {}, cities: {} };
    return cached;
  }
  cached = JSON.parse(fs.readFileSync(INTROS_PATH, 'utf8'));
  cached.states ||= {};
  cached.cities ||= {};
  return cached;
}

export function stateIntro(stateSlug) {
  return load().states[stateSlug]?.intro || null;
}

export function cityIntro(stateSlug, citySlug) {
  return load().cities[`${stateSlug}/${citySlug}`]?.intro || null;
}
