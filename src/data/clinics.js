// Loads clinic data from data/clinics.min.json at build time.
// The JSON file is produced by scripts/scrape_places.py then classified by
// scripts/enrich_clinics.py. loadClinics() returns only records classified as
// TRT-relevant (primary_trt or offers_trt); unrelated or unclassified records
// are excluded from the public directory.
//
// Shape (per clinic):
//   {
//     placeId: string,           // Google Places place_id (primary key)
//     slug: string,              // URL slug within city (e.g. "low-t-center")
//     name: string,
//     address: string,           // formatted address
//     street?: string,
//     city: string,
//     citySlug: string,          // e.g. "dallas"
//     state: string,             // two-letter abbr (e.g. "TX")
//     stateSlug: string,         // e.g. "texas"
//     zip?: string,
//     lat?: number,
//     lng?: number,
//     phone?: string,
//     website?: string,
//     rating?: number,
//     ratingCount?: number,
//     priceLevel?: "$"|"$$"|"$$$"|"$$$$",
//     googleUrl?: string,
//     hours?: string[],          // weekday text array from Places
//     services?: string[],       // derived tags (TRT, HRT, peptides, GLP-1, wellness)
//     types?: string[],          // raw Google Places types
//     verified?: boolean,        // manual editorial flag
//     featured?: boolean,        // manual editorial flag
//     telehealth?: boolean,      // true = online-only (routed to /telehealth)
//     classification?: "primary_trt"|"offers_trt"|"unrelated",
//     classificationConfidence?: "high"|"medium"|"low",
//     classificationReason?: string,
//     classificationModel?: string,
//     classificationAt?: string, // ISO date
//     source: "google-places" | "chain:<key>",
//     chain?: string,            // set when record is a known chain member (e.g. "Gameday Men's Health"); applies to chain-sourced records and enriched Places records alike
//     lastSeenAt: string,        // ISO date
//   }
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_PATH = path.join(__dirname, '..', '..', 'data', 'clinics.min.json');

const DIRECTORY_CLASSES = new Set(['primary_trt', 'offers_trt']);

let cached = null;

export function loadClinics() {
  if (cached) return cached;
  if (!fs.existsSync(DATA_PATH)) {
    cached = [];
    return cached;
  }
  const raw = fs.readFileSync(DATA_PATH, 'utf8');
  const parsed = JSON.parse(raw);
  const all = Array.isArray(parsed) ? parsed : (parsed.clinics || []);
  // Include CLOSED_PERMANENTLY so their detail pages still build (they render as
  // "permanently closed" pages with noindex + alternative-clinic suggestions) -
  // handy when someone searches the specific clinic name. Listing/roll-up
  // helpers below filter them out so they don't clutter live listings.
  cached = all.filter((c) => DIRECTORY_CLASSES.has(c.classification));
  return cached;
}

export function isPermanentlyClosed(clinic) {
  return clinic?.businessStatus === 'CLOSED_PERMANENTLY';
}

export function isTemporarilyClosed(clinic) {
  return clinic?.businessStatus === 'CLOSED_TEMPORARILY';
}

export function loadAllClinics() {
  if (!fs.existsSync(DATA_PATH)) return [];
  const raw = fs.readFileSync(DATA_PATH, 'utf8');
  const parsed = JSON.parse(raw);
  return Array.isArray(parsed) ? parsed : (parsed.clinics || []);
}

export function clinicsByState() {
  const byState = {};
  for (const c of loadClinics()) {
    if (c.telehealth) continue;
    if (isPermanentlyClosed(c)) continue;
    if (!c.stateSlug) continue;
    (byState[c.stateSlug] ||= []).push(c);
  }
  return byState;
}

export function clinicsByCity(stateSlug) {
  const byCity = {};
  for (const c of loadClinics()) {
    if (c.telehealth) continue;
    if (isPermanentlyClosed(c)) continue;
    if (c.stateSlug !== stateSlug) continue;
    if (!c.citySlug) continue;
    (byCity[c.citySlug] ||= []).push(c);
  }
  return byCity;
}

export function countsByState() {
  const counts = {};
  for (const c of loadClinics()) {
    if (c.telehealth || !c.stateSlug) continue;
    if (isPermanentlyClosed(c)) continue;
    counts[c.stateSlug] = (counts[c.stateSlug] ?? 0) + 1;
  }
  return counts;
}

// Chain field may be a comma-joined compound like "Gameday Men's Health, Biote Certified".
// Return the first token (primary chain) for badge display.
export function primaryChain(clinic) {
  if (!clinic?.chain) return null;
  const first = String(clinic.chain).split(',')[0].trim();
  return first || null;
}

export function isBiote(clinic) {
  if (!clinic) return false;
  if (clinic.biote) return true;
  if (typeof clinic.chain === 'string' && clinic.chain.toLowerCase().includes('biote')) return true;
  return false;
}

// Bayesian-ish score to surface well-reviewed clinics over single 5★ records.
// score = rating * log10(1 + reviewCount); min 20 reviews keeps it honest.
export function topRatedClinics({ limit = 12, minReviews = 20 } = {}) {
  const pool = loadClinics().filter((c) =>
    !c.telehealth && !isPermanentlyClosed(c) && c.rating && (c.ratingCount ?? 0) >= minReviews && c.stateSlug && c.citySlug && c.slug
  );
  pool.sort((a, b) => {
    const score = (c) => (c.rating || 0) * Math.log10(1 + (c.ratingCount || 0));
    return score(b) - score(a);
  });
  // Diversify by state so the list isn't all-Texas
  const seen = new Set();
  const out = [];
  for (const c of pool) {
    if (seen.has(c.stateSlug)) continue;
    out.push(c);
    seen.add(c.stateSlug);
    if (out.length >= limit) break;
  }
  return out;
}

// Flatten the Places amenity blobs into a short list the UI can render with
// icons. Each amenity is { key, icon, label }; returns [] when nothing is
// populated so the caller can skip the whole section.
export function amenities(clinic) {
  if (!clinic) return [];
  const out = [];
  const a = clinic.accessibility || {};
  if (a.wheelchairAccessibleEntrance || a.wheelchairAccessibleParking) {
    out.push({ key: 'accessible', icon: 'accessibility', label: 'Wheelchair accessible' });
  }
  const p = clinic.parking || {};
  if (p.freeParkingLot || p.freeStreetParking || p.freeGarageParking) {
    out.push({ key: 'parking-free', icon: 'parking', label: 'Free parking' });
  } else if (p.paidParkingLot || p.paidStreetParking || p.paidGarageParking || p.valetParking) {
    out.push({ key: 'parking-paid', icon: 'parking', label: 'Paid parking' });
  }
  const pay = clinic.payment || {};
  if (pay.acceptsCreditCards) {
    out.push({ key: 'cards', icon: 'credit-card', label: 'Credit cards accepted' });
  }
  if (pay.acceptsNfc) {
    out.push({ key: 'contactless', icon: 'wifi-tethering', label: 'Contactless payment' });
  }
  return out;
}

export function directorySummary() {
  const all = loadClinics().filter((c) => !c.telehealth && !isPermanentlyClosed(c));
  const rated = all.filter((c) => c.rating);
  const avgRating = rated.length
    ? rated.reduce((s, c) => s + (c.rating || 0), 0) / rated.length
    : 0;
  const states = new Set(all.map((c) => c.stateSlug).filter(Boolean));
  const cities = new Set(all.map((c) => `${c.stateSlug}/${c.citySlug}`).filter((k) => k && !k.startsWith('/')));
  return {
    total: all.length,
    states: states.size,
    cities: cities.size,
    avgRating,
  };
}
