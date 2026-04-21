#!/usr/bin/env python3
"""
scrape_yelp.py — discover TRT/HRT clinics via the Yelp Fusion /businesses/search API.

A different index than Google Places. Overlap is high, but Yelp surfaces some
businesses Places doesn't (especially smaller concierge practices), and it gives
us authoritative Yelp review data to enrich the records we already have.

Seeds: same (state, metro) grid as Places. Terms are TRT-focused. Each (metro,
term) call returns up to 50 results; we paginate via `offset` up to 240.

Output is merged into public/data/clinics.min.json using three-tier dedup:
street+city+state → phone+city+state → (normalized name)+city+state. New
records get synthetic placeId `yelp-<yelp_id>` and `source: yelp`. Existing
records matched via Yelp get `yelp_id`, `yelp_rating`, `yelp_review_count`,
`yelp_url` attached (opportunistic enrichment).

Usage:
  python3 scripts/scrape_yelp.py --states TX,CA --max-offsets 2  # pilot
  python3 scripts/scrape_yelp.py                                 # full run
  python3 scripts/scrape_yelp.py --dry-run --limit 10
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
DATA_FILE = os.path.join(ROOT, 'public', 'data', 'clinics.min.json')
METROS_FILE = os.path.join(ROOT, 'data', 'seed-metros.json')
ENV_FILE = os.path.join(ROOT, '.env')

YELP_URL = 'https://api.yelp.com/v3/businesses/search'
PAGE_SIZE = 50  # Yelp max per request
HARD_OFFSET_CAP = 240  # Yelp caps total results per query at ~240
SAVE_EVERY = 200
SLEEP_BETWEEN = 0.15  # Be a good citizen; Yelp free tier has request limits

# Short, focused list — Yelp's search is term-based, not just exact match, so a
# few well-chosen terms cover the space. Too many overlapping terms burns our
# daily quota without surfacing new businesses.
DEFAULT_TERMS = [
    'testosterone replacement therapy',
    'TRT clinic',
    "men's health clinic",
    'hormone therapy',
    'low testosterone',
]

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


def load_env():
    """Load KEY=VAL lines from .env (simple parser, no dependencies)."""
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)


def slugify(s):
    s = (s or '').lower()
    s = re.sub(r"[^a-z0-9\s-]+", '', s)
    s = re.sub(r"\s+", '-', s.strip())
    s = re.sub(r"-+", '-', s).strip('-')
    return s


def norm_addr_key(street, city, state):
    s = (street or '').lower()
    s = re.sub(r'\b(suite|ste\.?|#|unit|apt\.?|bldg|building|floor|fl\.?)\b[^,]*', '', s)
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    c = slugify(city or '')
    st = (state or '').upper()
    return f'{s}|{c}|{st}'


def norm_phone(phone):
    if not phone:
        return None
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('1') and len(digits) == 11:
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def norm_name_key(name, city, state):
    """For fuzzy dedup: normalize clinic name by stripping location suffixes + common filler words."""
    n = (name or '').lower()
    # Strip trailing '— Dallas, TX' / 'of Dallas' style suffixes
    n = re.sub(r'\s*[-–—|]\s*[a-z\s,]+$', '', n)
    n = re.sub(r'\s+of\s+[a-z\s]+$', '', n)
    # Strip common filler nouns
    for w in ('clinic','center','centre','medical','llc','inc','pc','pa','and','the'):
        n = re.sub(rf'\b{w}\b', '', n)
    n = re.sub(r'[^a-z0-9 ]+', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return f'{n}|{slugify(city or "")}|{(state or "").upper()}'


def format_phone(raw):
    d = norm_phone(raw)
    if not d:
        return raw
    return f'({d[0:3]}) {d[3:6]}-{d[6:10]}'


def yelp_search(key, term, location, offset=0, retry=2):
    params = urllib.parse.urlencode({
        'term': term,
        'location': location,
        'limit': PAGE_SIZE,
        'offset': offset,
    })
    req = urllib.request.Request(
        f'{YELP_URL}?{params}',
        headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            remaining = resp.headers.get('RateLimit-Remaining')
            data = json.loads(resp.read())
            return data, remaining
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:200]
        if e.code == 429 and retry > 0:
            time.sleep(3)
            return yelp_search(key, term, location, offset, retry - 1)
        raise RuntimeError(f'Yelp HTTP {e.code}: {body}')


def normalize_biz(biz):
    """Turn a Yelp search result into a clinic record (or None if location is missing/non-US)."""
    loc = biz.get('location') or {}
    street = (loc.get('address1') or '').strip()
    a2 = (loc.get('address2') or '').strip()
    if a2:
        street = f'{street} {a2}'.strip()
    city = (loc.get('city') or '').strip()
    state = (loc.get('state') or '').strip().upper()
    zip_code = (loc.get('zip_code') or '').strip()
    if not state or state not in US_STATES:
        return None
    if loc.get('country') and loc['country'] != 'US':
        return None
    coords = biz.get('coordinates') or {}
    lat = coords.get('latitude')
    lng = coords.get('longitude')
    name = biz.get('name') or ''
    full_addr = loc.get('display_address') or []
    full_addr = ', '.join(full_addr) if full_addr else f'{street}, {city}, {state} {zip_code}'.strip(', ')
    yelp_id = biz.get('id')
    state_name = US_STATES.get(state)
    cats = [c.get('alias') for c in (biz.get('categories') or []) if c.get('alias')]
    now_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    return {
        'placeId': f'yelp-{yelp_id}',
        'yelpId': yelp_id,
        'name': name,
        'slug': slugify(name) or yelp_id[:12],
        'address': full_addr,
        'street': street or None,
        'city': city or None,
        'citySlug': slugify(city) if city else None,
        'state': state,
        'stateSlug': slugify(state_name) if state_name else None,
        'zip': zip_code or None,
        'lat': lat,
        'lng': lng,
        'phone': format_phone(biz.get('phone')) if biz.get('phone') else None,
        'website': None,  # Yelp search API doesn't expose the business's own website
        'rating': None,   # Google rating; keep distinct from Yelp rating
        'ratingCount': None,
        'priceLevel': biz.get('price'),
        'googleUrl': None,
        'hours': [],
        'services': ['TRT'],
        'types': cats,
        'verified': False,
        'featured': False,
        'telehealth': False,
        'yelpRating': biz.get('rating'),
        'yelpReviewCount': biz.get('review_count'),
        'yelpUrl': biz.get('url'),
        'source': 'yelp',
        'lastSeenAt': now_iso,
    }


def build_indices(records):
    by_addr = {}
    by_phone = {}
    by_name = {}
    by_place = {}
    for r in records:
        pid = r.get('placeId')
        if pid:
            by_place[pid] = r
        if r.get('street'):
            ak = norm_addr_key(r.get('street'), r.get('city'), r.get('state'))
            if ak and ak != '||':
                by_addr[ak] = r
        pd = norm_phone(r.get('phone'))
        if pd:
            by_phone.setdefault(pd, r)
        nk = norm_name_key(r.get('name'), r.get('city'), r.get('state'))
        if nk and nk.split('|')[0]:
            by_name.setdefault(nk, []).append(r)
    return by_place, by_addr, by_phone, by_name


def apply_yelp_to_existing(existing, yelp_rec):
    """Attach Yelp id + review data to an existing Places record; fill missing gaps."""
    changed = False
    for yk, ek in (('yelpId', 'yelpId'),
                   ('yelpRating', 'yelpRating'),
                   ('yelpReviewCount', 'yelpReviewCount'),
                   ('yelpUrl', 'yelpUrl')):
        if yelp_rec.get(yk) and not existing.get(ek):
            existing[ek] = yelp_rec[yk]; changed = True
    for field in ('street', 'zip', 'lat', 'lng', 'phone'):
        if not existing.get(field) and yelp_rec.get(field):
            existing[field] = yelp_rec[field]; changed = True
    if existing.get('source') == 'yelp':
        existing['lastSeenAt'] = yelp_rec['lastSeenAt']
    return changed


def load_existing():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE) as f:
        data = json.load(f)
    return data if isinstance(data, list) else (data.get('clinics') or [])


def save(records):
    records.sort(key=lambda c: (c.get('stateSlug') or '', c.get('citySlug') or '', c.get('name') or ''))
    with open(DATA_FILE, 'w') as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f'[save] wrote {len(records)} clinics → {DATA_FILE}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--states', type=str, default='', help='Comma-sep state abbrs; default = all states in seed-metros.json')
    ap.add_argument('--metros', type=str, default='', help='Override metros (comma-sep); only when --states has one value')
    ap.add_argument('--terms', type=str, default='', help='Override terms (comma-sep)')
    ap.add_argument('--max-offsets', type=int, default=1, help='Offset pages per (metro, term); 1 = first 50 results only')
    ap.add_argument('--limit', type=int, default=0, help='Stop after N total API calls (pilot knob)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    load_env()
    api_key = os.environ.get('YELP_API_KEY')
    if not api_key:
        print('[err] YELP_API_KEY not set (.env or env var)'); sys.exit(2)

    with open(METROS_FILE) as f:
        metros_by_state = json.load(f)['metros']

    wanted_states = [s.strip().upper() for s in args.states.split(',') if s.strip()] or list(metros_by_state.keys())
    terms = [t.strip() for t in args.terms.split(',') if t.strip()] or DEFAULT_TERMS

    existing = load_existing()
    print(f'[start] loaded {len(existing)} existing clinics')
    by_place, by_addr, by_phone, by_name = build_indices(existing)

    added = 0
    enriched = 0
    raw_seen = 0
    call_count = 0
    seen_yelp_ids = set()

    try:
        for st in wanted_states:
            state_name = US_STATES.get(st)
            if not state_name:
                continue
            metros = [m.strip() for m in args.metros.split(',') if m.strip()] \
                     if (args.metros and len(wanted_states) == 1) else metros_by_state.get(st, [])
            if not metros:
                print(f'[skip] no metros for {st}')
                continue

            st_added = 0; st_enriched = 0
            for metro in metros:
                location = f'{metro}, {state_name}'
                for term in terms:
                    for page in range(args.max_offsets):
                        offset = page * PAGE_SIZE
                        if offset >= HARD_OFFSET_CAP:
                            break
                        if args.limit and call_count >= args.limit:
                            raise StopIteration
                        try:
                            data, remaining = yelp_search(api_key, term, location, offset=offset)
                            call_count += 1
                        except Exception as e:
                            print(f'[err] {location} / {term!r} off={offset}: {e}')
                            break
                        businesses = data.get('businesses') or []
                        added_this = 0; enriched_this = 0
                        for biz in businesses:
                            yid = biz.get('id')
                            if not yid or yid in seen_yelp_ids:
                                continue
                            seen_yelp_ids.add(yid)
                            raw_seen += 1
                            norm = normalize_biz(biz)
                            if not norm:
                                continue

                            # Dedup: yelp id already a record? placeId already present? addr/phone/name match existing Places record?
                            match = by_place.get(norm['placeId'])
                            how = 'yelp-id' if match else None
                            if not match and norm.get('street'):
                                ak = norm_addr_key(norm['street'], norm['city'], norm['state'])
                                if ak in by_addr:
                                    match = by_addr[ak]; how = 'addr'
                            if not match:
                                pd = norm_phone(norm.get('phone'))
                                if pd and pd in by_phone:
                                    cand = by_phone[pd]
                                    if slugify(cand.get('city') or '') == slugify(norm.get('city') or '') \
                                       and (cand.get('state') or '') == norm.get('state'):
                                        match = cand; how = 'phone'
                            if not match:
                                nk = norm_name_key(norm['name'], norm['city'], norm['state'])
                                candidates = by_name.get(nk, [])
                                if candidates:
                                    match = candidates[0]; how = 'name'

                            if match:
                                if apply_yelp_to_existing(match, norm):
                                    enriched += 1; enriched_this += 1; st_enriched += 1
                                continue

                            existing.append(norm)
                            by_place[norm['placeId']] = norm
                            if norm.get('street'):
                                by_addr[norm_addr_key(norm['street'], norm['city'], norm['state'])] = norm
                            pd = norm_phone(norm.get('phone'))
                            if pd:
                                by_phone.setdefault(pd, norm)
                            nk = norm_name_key(norm['name'], norm['city'], norm['state'])
                            by_name.setdefault(nk, []).append(norm)
                            added += 1; added_this += 1; st_added += 1

                        print(f'[q] {location!r} / {term!r} off={offset}: '
                              f'{len(businesses)} biz, +{added_this} new, ~{enriched_this} enriched, '
                              f'rl_remaining={remaining}')

                        if not args.dry_run and call_count % SAVE_EVERY == 0 and call_count > 0:
                            save(existing)
                            print(f'[checkpoint] {added} new / {enriched} enriched at {call_count} calls')

                        time.sleep(SLEEP_BETWEEN)
                        if len(businesses) < PAGE_SIZE:
                            break
            print(f'[state] {st}: +{st_added} new, ~{st_enriched} enriched')
    except StopIteration:
        print(f'[limit] stopped at --limit {args.limit}')

    print(f'\n[summary] {call_count} API calls, {raw_seen} raw biz seen, +{added} new, ~{enriched} existing enriched')

    if args.dry_run:
        print('[dry-run] skipping save')
        return
    save(existing)


if __name__ == '__main__':
    main()
