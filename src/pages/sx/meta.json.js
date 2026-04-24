// Typeahead meta shard: cities, chain rollups, and the list of state shards.
// Individual clinic names are NOT in this file by design - getting the full
// clinic list requires fetching per-state shards at /sx/s/{stateSlug}.json.
import { loadClinics, isPermanentlyClosed } from '../../data/clinics.js';
import { STATE_BY_SLUG } from '../../data/states.js';

export async function GET() {
  const all = loadClinics().filter((c) => !c.telehealth && !isPermanentlyClosed(c) && c.stateSlug && c.citySlug && c.slug);

  const cityMap = new Map();
  const chainCounts = new Map();
  const stateSet = new Set();

  for (const c of all) {
    const stateAbbr = STATE_BY_SLUG[c.stateSlug]?.abbr || '';
    stateSet.add(c.stateSlug);

    const cityKey = `${c.stateSlug}/${c.citySlug}`;
    if (!cityMap.has(cityKey)) {
      cityMap.set(cityKey, { name: c.city, stateAbbr, stateSlug: c.stateSlug, citySlug: c.citySlug, count: 0 });
    }
    cityMap.get(cityKey).count++;

    const chainRaw = c.chain ? String(c.chain).split(',')[0].trim() : '';
    if (chainRaw) {
      chainCounts.set(chainRaw, (chainCounts.get(chainRaw) || 0) + 1);
    }
  }

  const cities = [...cityMap.values()]
    .sort((a, b) => b.count - a.count)
    .map((c) => [c.name, c.stateAbbr, c.stateSlug, c.citySlug, c.count]);

  const chains = [...chainCounts.entries()]
    .filter(([, n]) => n >= 3)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => [name, count]);

  const states = [...stateSet].sort();

  return new Response(JSON.stringify({ cities, chains, states }), {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}
