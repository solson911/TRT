// Per-state clinic shard. One file per state slug; clients fetch only the
// shards they need (typeahead loads on demand, /search and /near load all in
// parallel). Sharding keeps the typeahead bundle small and forces scrapers
// to make ~50 fetches instead of one bulk dump.
//
// Row shape (kept positional to match client decoders):
//   [name, city, stateAbbr, stateSlug, citySlug, slug, score]
import { loadClinics, isPermanentlyClosed } from '../../../data/clinics.js';
import { STATES, STATE_BY_SLUG } from '../../../data/states.js';

export function getStaticPaths() {
  return STATES.map((s) => ({ params: { state: s.slug } }));
}

export async function GET({ params }) {
  const { state } = params;
  const stateInfo = STATE_BY_SLUG[state];
  if (!stateInfo) {
    return new Response(JSON.stringify({ error: 'unknown state' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  }

  const clinics = loadClinics().filter(
    (c) => !c.telehealth && !isPermanentlyClosed(c) && c.stateSlug === state && c.citySlug && c.slug
  );

  const rows = clinics.map((c) => {
    const score = (c.rating || 0) * Math.log10(1 + (c.ratingCount || 0));
    return [c.name, c.city, stateInfo.abbr, c.stateSlug, c.citySlug, c.slug, Math.round(score * 10) / 10, c.lat || 0, c.lng || 0];
  });

  return new Response(JSON.stringify({ state, rows }), {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}
