#!/usr/bin/env python3
"""
scrape_places.py — discover TRT/HRT clinics via Google Places (New) Text Search.

Seeds: data/seed-queries.json x data/seed-metros.json. For each (query, metro, state),
runs Places Text Search, paginates with nextPageToken, and appends unique entries
(by place_id) to data/clinics.min.json.

Usage:
  python3 scripts/scrape_places.py                     # all states
  python3 scripts/scrape_places.py --states TX,CA,FL   # pilot
  python3 scripts/scrape_places.py --states TX --metros "Dallas,Austin" --dry-run
  python3 scripts/scrape_places.py --max-pages 1       # cheaper, fewer results
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'data', 'clinics.min.json')
QUERIES_FILE = os.path.join(ROOT, 'data', 'seed-queries.json')
METROS_FILE = os.path.join(ROOT, 'data', 'seed-metros.json')

# Server-side calls (no HTTP Referer) must use the unrestricted/IP-restricted
# key from .env, not the referer-restricted browser key. Prefer env over the
# legacy hardcoded fallback.
API_KEY = (
    os.environ.get('PLACES_UNRESTRICTED_API_KEY')
    or os.environ.get('GOOGLE_PLACES_API_KEY')
    or 'AIzaSyCIrnX_thibzANXFSbkyu04cFWoWMjK718'
)
SEARCH_URL = 'https://places.googleapis.com/v1/places:searchText'

# Flush to disk every N API calls so a crash or disconnect doesn't lose a whole sweep.
SAVE_EVERY = 200

FIELD_MASK = ','.join([
    'places.id',
    'places.displayName',
    'places.formattedAddress',
    'places.shortFormattedAddress',
    'places.addressComponents',
    'places.location',
    'places.nationalPhoneNumber',
    'places.websiteUri',
    'places.rating',
    'places.userRatingCount',
    'places.priceLevel',
    'places.googleMapsUri',
    'places.types',
    'places.primaryType',
    'places.primaryTypeDisplayName',
    'places.businessStatus',
    'places.regularOpeningHours',
    'places.photos',
    'places.editorialSummary',
    'places.generativeSummary',
    'places.reviews',
    'places.accessibilityOptions',
    'places.paymentOptions',
    'places.parkingOptions',
    'places.priceRange',
    'nextPageToken',
])

# Two-letter abbr -> full state name, for the reverse lookup below.
US_STATES = {
    'AL':'Alabama','AK':'Alaska','AZ':'Arizona','AR':'Arkansas','CA':'California',
    'CO':'Colorado','CT':'Connecticut','DE':'Delaware','FL':'Florida','GA':'Georgia',
    'HI':'Hawaii','ID':'Idaho','IL':'Illinois','IN':'Indiana','IA':'Iowa','KS':'Kansas',
    'KY':'Kentucky','LA':'Louisiana','ME':'Maine','MD':'Maryland','MA':'Massachusetts',
    'MI':'Michigan','MN':'Minnesota','MS':'Mississippi','MO':'Missouri','MT':'Montana',
    'NE':'Nebraska','NV':'Nevada','NH':'New Hampshire','NJ':'New Jersey','NM':'New Mexico',
    'NY':'New York','NC':'North Carolina','ND':'North Dakota','OH':'Ohio','OK':'Oklahoma',
    'OR':'Oregon','PA':'Pennsylvania','RI':'Rhode Island','SC':'South Carolina',
    'SD':'South Dakota','TN':'Tennessee','TX':'Texas','UT':'Utah','VT':'Vermont',
    'VA':'Virginia','WA':'Washington','WV':'West Virginia','WI':'Wisconsin','WY':'Wyoming',
}


def slugify(s):
    s = (s or '').lower()
    s = re.sub(r"[^a-z0-9\s-]+", '', s)
    s = re.sub(r"\s+", '-', s.strip())
    s = re.sub(r"-+", '-', s).strip('-')
    return s


def http_post(url, body, headers, timeout=20):
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def places_text_search(query, page_token=None):
    body = {'textQuery': query, 'regionCode': 'US', 'pageSize': 20}
    if page_token:
        body['pageToken'] = page_token
    headers = {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': API_KEY,
        'X-Goog-FieldMask': FIELD_MASK,
    }
    return http_post(SEARCH_URL, body, headers)


def extract_address_parts(place):
    """Pull city, state, zip out of addressComponents (new API returns a list)."""
    city = state = zip_code = street = None
    for comp in place.get('addressComponents', []) or []:
        types = comp.get('types', []) or []
        val = comp.get('longText') or comp.get('shortText')
        if 'locality' in types or 'postal_town' in types:
            city = city or val
        elif 'sublocality' in types and not city:
            city = val
        elif 'administrative_area_level_1' in types:
            state = comp.get('shortText') or val
        elif 'postal_code' in types:
            zip_code = val
    return street, city, state, zip_code


SERVICE_KEYWORDS = [
    ('TRT', [r'\btrt\b', r'testosterone']),
    ('HRT', [r'\bhrt\b', r'hormone\s+replacement', r'bioidentical']),
    ('Peptides', [r'peptide', r'bpc-?157', r'ghk-?cu', r'ipamorelin']),
    ('GLP-1', [r'glp-?1', r'semaglutide', r'tirzepatide']),
    ('Wellness', [r'wellness', r'longevity', r'anti-?aging']),
    ('IV Therapy', [r'\biv\b', r'intravenous']),
]


def derive_services(name, types):
    hay = ' '.join([name or ''] + list(types or [])).lower()
    found = []
    for label, patterns in SERVICE_KEYWORDS:
        for p in patterns:
            if re.search(p, hay):
                found.append(label)
                break
    # Always tag TRT if none matched — every clinic in this directory is a TRT candidate
    if not found:
        found.append('TRT')
    return found


def price_level_to_symbol(level):
    if not level:
        return None
    mapping = {
        'PRICE_LEVEL_FREE': 'Free',
        'PRICE_LEVEL_INEXPENSIVE': '$',
        'PRICE_LEVEL_MODERATE': '$$',
        'PRICE_LEVEL_EXPENSIVE': '$$$',
        'PRICE_LEVEL_VERY_EXPENSIVE': '$$$$',
    }
    return mapping.get(level)


def normalize_place(place, fallback_state):
    pid = place.get('id')
    if not pid:
        return None
    name = (place.get('displayName') or {}).get('text') or ''
    formatted = place.get('formattedAddress')
    street, city, state_abbr, zip_code = extract_address_parts(place)
    if not state_abbr:
        state_abbr = fallback_state
    state_name = US_STATES.get(state_abbr)
    loc = place.get('location') or {}
    hours = ((place.get('regularOpeningHours') or {}).get('weekdayDescriptions') or [])
    services = derive_services(name, place.get('types') or [])

    return {
        'placeId': pid,
        'name': name,
        'slug': slugify(name) or pid[:12],
        'address': formatted,
        'street': street,
        'city': city,
        'citySlug': slugify(city) if city else None,
        'state': state_abbr,
        'stateSlug': slugify(state_name) if state_name else None,
        'zip': zip_code,
        'lat': loc.get('latitude'),
        'lng': loc.get('longitude'),
        'phone': place.get('nationalPhoneNumber'),
        'website': place.get('websiteUri'),
        'rating': place.get('rating'),
        'ratingCount': place.get('userRatingCount'),
        'priceLevel': price_level_to_symbol(place.get('priceLevel')),
        'googleUrl': place.get('googleMapsUri'),
        'hours': hours,
        'services': services,
        'types': place.get('types') or [],
        'verified': False,
        'featured': False,
        'telehealth': False,
        'source': 'google-places',
        'lastSeenAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def load_existing():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {c['placeId']: c for c in data if c.get('placeId')}
        if isinstance(data, dict) and 'clinics' in data:
            return {c['placeId']: c for c in data['clinics'] if c.get('placeId')}
    except Exception as e:
        print(f'[warn] could not read existing data: {e}')
    return {}


def save_all(by_id):
    items = sorted(by_id.values(), key=lambda c: (c.get('stateSlug') or '', c.get('citySlug') or '', c.get('name') or ''))
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, 'w') as f:
        json.dump(items, f, ensure_ascii=False, separators=(',', ':'))
    print(f'[save] wrote {len(items)} clinics → {DATA_FILE}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--states', type=str, default='', help='Comma-sep state abbrs (e.g. TX,CA,FL); default = all')
    ap.add_argument('--metros', type=str, default='', help='Override metros (comma-sep); applies to single --states value')
    ap.add_argument('--queries', type=str, default='', help='Override queries (comma-sep)')
    ap.add_argument('--max-pages', type=int, default=2, help='Max paginated pages per query (Places caps ~3 @ 20 results each)')
    ap.add_argument('--mode', choices=['metros', 'statewide'], default='metros',
                    help='metros = (query near metro, state); statewide = (query in state). Run separately to avoid duplicate API spend.')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    with open(QUERIES_FILE) as f:
        default_queries = json.load(f)['queries']
    with open(METROS_FILE) as f:
        metros_by_state = json.load(f)['metros']

    queries = [q.strip() for q in args.queries.split(',') if q.strip()] or default_queries
    wanted_states = [s.strip().upper() for s in args.states.split(',') if s.strip()] or list(metros_by_state.keys())

    by_id = load_existing()
    before = len(by_id)
    call_count = 0
    print(f'[start] loaded {before} existing clinics; scanning {len(wanted_states)} state(s)')

    for st in wanted_states:
        state_name = US_STATES.get(st, st)
        new_in_state = 0

        if args.mode == 'statewide':
            location_seeds = [None]  # single statewide pass; no metro context
        else:
            metros = [m.strip() for m in args.metros.split(',') if m.strip()] if (args.metros and len(wanted_states) == 1) else metros_by_state.get(st, [])
            if not metros:
                print(f'[skip] no metros configured for {st}')
                continue
            location_seeds = metros

        for seed in location_seeds:
            for q in queries:
                full_query = f'{q} in {state_name}' if seed is None else f'{q} near {seed}, {state_name}'
                token = None
                for page in range(args.max_pages):
                    try:
                        resp = places_text_search(full_query, page_token=token)
                        call_count += 1
                    except Exception as e:
                        print(f'[err] {full_query} (page {page}): {e}')
                        break
                    if not args.dry_run and call_count % SAVE_EVERY == 0:
                        save_all(by_id)
                        print(f'[checkpoint] {len(by_id)} clinics persisted at {call_count} API calls')
                    places = resp.get('places') or []
                    added = 0
                    for p in places:
                        norm = normalize_place(p, st)
                        if not norm:
                            continue
                        if norm['placeId'] not in by_id:
                            by_id[norm['placeId']] = norm
                            added += 1
                            new_in_state += 1
                    print(f'[q] {full_query!r} page {page+1}: {len(places)} results, +{added} new')
                    token = resp.get('nextPageToken')
                    if not token:
                        break
                    # Places requires a short delay before reusing nextPageToken
                    time.sleep(2)
        print(f'[state] {st}: +{new_in_state} new')

    after = len(by_id)
    print(f'[done] {after - before} new clinics discovered ({before} → {after})')
    if args.dry_run:
        print('[dry-run] skipping save')
    else:
        save_all(by_id)


if __name__ == '__main__':
    main()
