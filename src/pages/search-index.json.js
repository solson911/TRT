// Static search index for the homepage typeahead. Generated at build time.
// Format is compact positional arrays to keep the payload small:
//   clinics: [name, city, stateAbbr, stateSlug, citySlug, slug, score]
//   cities:  [cityName, stateAbbr, stateSlug, citySlug, clinicCount]
import { loadClinics, isPermanentlyClosed, primaryChain } from '../data/clinics.js';
import { STATE_BY_SLUG } from '../data/states.js';

export async function GET() {
  const all = loadClinics().filter((c) => !c.telehealth && !isPermanentlyClosed(c) && c.stateSlug && c.citySlug && c.slug);

  const cityMap = new Map();
  const clinics = [];
  for (const c of all) {
    const stateAbbr = STATE_BY_SLUG[c.stateSlug]?.abbr || '';
    const cityKey = `${c.stateSlug}/${c.citySlug}`;
    if (!cityMap.has(cityKey)) {
      cityMap.set(cityKey, { name: c.city, stateAbbr, stateSlug: c.stateSlug, citySlug: c.citySlug, count: 0 });
    }
    cityMap.get(cityKey).count++;
    const score = (c.rating || 0) * Math.log10(1 + (c.ratingCount || 0));
    clinics.push([
      c.name,
      c.city || '',
      stateAbbr,
      c.stateSlug,
      c.citySlug,
      c.slug,
      Math.round(score * 10) / 10,
    ]);
  }

  const cities = [...cityMap.values()]
    .sort((a, b) => b.count - a.count)
    .map((c) => [c.name, c.stateAbbr, c.stateSlug, c.citySlug, c.count]);

  return new Response(JSON.stringify({ cities, clinics }), {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}
