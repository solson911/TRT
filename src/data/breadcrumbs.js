// Breadcrumb helpers. Each crumb is { name, url }. URLs are absolute so the
// JSON-LD version is valid on its own. Callers render the visible nav from
// the same data to keep the two in lockstep.

const SITE = 'https://trtindex.com';

export function clinicCrumbs(clinic, stateName) {
  return [
    { name: 'Home', url: `${SITE}/` },
    { name: 'States', url: `${SITE}/clinics/` },
    { name: stateName, url: `${SITE}/clinics/${clinic.stateSlug}/` },
    { name: clinic.city, url: `${SITE}/clinics/${clinic.stateSlug}/${clinic.citySlug}/` },
    { name: clinic.name, url: `${SITE}/clinics/${clinic.stateSlug}/${clinic.citySlug}/${clinic.slug}/` },
  ];
}

export function stateCrumbs(stateName, stateSlug) {
  return [
    { name: 'Home', url: `${SITE}/` },
    { name: 'States', url: `${SITE}/clinics/` },
    { name: stateName, url: `${SITE}/clinics/${stateSlug}/` },
  ];
}

export function cityCrumbs(cityName, citySlug, stateName, stateSlug) {
  return [
    { name: 'Home', url: `${SITE}/` },
    { name: 'States', url: `${SITE}/clinics/` },
    { name: stateName, url: `${SITE}/clinics/${stateSlug}/` },
    { name: cityName, url: `${SITE}/clinics/${stateSlug}/${citySlug}/` },
  ];
}

export function breadcrumbJsonLd(crumbs) {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: crumbs.map((c, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: c.name,
      item: c.url,
    })),
  };
}

// Summary-page ItemList: each item is just a URL + position + name. This is
// the shape Google recommends for directory/listing pages where each entry
// links to a detail page.
export function summaryItemListJsonLd(items, { name } = {}) {
  return {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    ...(name && { name }),
    itemListElement: items.map((it, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      url: it.url,
      name: it.name,
    })),
  };
}
