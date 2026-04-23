// Loads telehealth brand data from data/telehealth.json (produced by
// scripts/scrape_telehealth.py -> extract_telehealth.py) and pairs it with
// editorial reviews from data/telehealth-reviews/{slug}.md (produced by
// scripts/write_telehealth_reviews.py).
//
// Shape (per brand):
//   {
//     slug: string,
//     name: string,
//     website: string,
//     tagline?: string,
//     founded?: number,
//     medicalDirector?: string,
//     statesCovered?: "all" | string[],
//     priceMin?: number,           // monthly USD
//     priceMax?: number,
//     pricingTiers?: [{ name, price, period, includes }],
//     prescriberModel?: "physician"|"PA-NP"|"mixed",
//     consultModel?: "async"|"sync"|"both",
//     labsIncluded?: boolean,
//     labsShipped?: boolean,
//     medicationsShipped?: boolean,
//     treatmentOptions?: string[], // ["injection","cream","pellets",...]
//     insurance?: boolean,
//     fsaHsa?: boolean,
//     pros?: string[],
//     cons?: string[],
//     review?: string,             // body markdown loaded from data/telehealth-reviews
//     extractedAt?: string,        // ISO date
//   }
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_FILE = path.join(__dirname, '..', '..', 'data', 'telehealth.json');
const REVIEWS_DIR = path.join(__dirname, '..', '..', 'data', 'telehealth-reviews');
const REDDIT_DIR = path.join(__dirname, '..', '..', 'data', 'telehealth-reddit');

let cached = null;

export function loadTelehealth() {
  if (cached) return cached;
  if (!fs.existsSync(DATA_FILE)) {
    cached = [];
    return cached;
  }
  const raw = fs.readFileSync(DATA_FILE, 'utf8');
  const parsed = JSON.parse(raw);
  const brands = Array.isArray(parsed) ? parsed : (parsed.brands || []);
  for (const b of brands) {
    const md = path.join(REVIEWS_DIR, `${b.slug}.md`);
    if (fs.existsSync(md)) {
      b.review = fs.readFileSync(md, 'utf8');
    }
    const reddit = path.join(REDDIT_DIR, `${b.slug}.md`);
    if (fs.existsSync(reddit)) {
      b.redditSummary = fs.readFileSync(reddit, 'utf8');
    }
  }
  // Only surface brands that have at least a review body OR a price signal.
  // A brand with neither is still an unreviewed stub and shouldn't go live.
  cached = brands.filter((b) => b.review || b.priceMin || b.priceMax);
  cached.sort((a, b) => a.name.localeCompare(b.name));
  return cached;
}

export function findTelehealth(slug) {
  return loadTelehealth().find((b) => b.slug === slug) || null;
}

export function telehealthSummary() {
  const all = loadTelehealth();
  const prices = all.map((b) => b.priceMin || b.priceMax).filter(Boolean);
  const minPrice = prices.length ? Math.min(...prices) : null;
  const withStates = all.filter((b) => b.statesCovered).length;
  return {
    total: all.length,
    minPrice,
    withStates,
  };
}

// Format pricing as a short chip. "From $99/mo" or "Quote only".
export function formatPrice(b) {
  if (b.priceMin && b.priceMax && b.priceMin !== b.priceMax) {
    return `$${b.priceMin}-$${b.priceMax}/mo`;
  }
  const p = b.priceMin || b.priceMax;
  if (p) return `From $${p}/mo`;
  return 'Quote only';
}

export function formatStates(b) {
  const s = b.statesCovered;
  if (s === 'all') return 'All 50 states';
  if (Array.isArray(s) && s.length) {
    if (s.length >= 45) return 'All 50 states';
    if (s.length > 8) return `${s.length} states`;
    return s.join(', ');
  }
  return 'Check site';
}
