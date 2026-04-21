#!/usr/bin/env python3
"""
scrape_chains.py — pull clinic rosters from known TRT chain websites.

These sites publish structured location data that's free to extract (no API
cost, public find-a-location pages). Each chain is parsed differently:

  - gameday     → JSON API at api.gamedaymenshealth.com/entities
  - lowtcenter  → inline JSON array on /locations/ page
  - renewvit    → window.locations JS array on vitalityhrt.com/location/
  - restore     → schema.org HealthAndBeautyBusiness ld+json on each
                  /locations/<slug> page (discovered via sitemap.xml)
  - serotonin   → inline DatoCMS "locations" JSON array on /locations/

Output gets merged into public/data/clinics.min.json.

Dedup: each chain record is assigned a synthetic placeId (`chain-<chain>-<id>`).
Before insertion we check whether the existing dataset already contains the
same physical location — matched by normalized street+city+state — and if so,
we skip adding a duplicate (the Google Places record is richer and wins).

Usage:
  python3 scripts/scrape_chains.py                      # all chains
  python3 scripts/scrape_chains.py --chains gameday,lowt   # scoped
  python3 scripts/scrape_chains.py --dry-run
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from html import unescape

sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'public', 'data', 'clinics.min.json')

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

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
STATE_NAME_TO_ABBR = {v.lower(): k for k, v in US_STATES.items()}


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def slugify(s):
    s = (s or '').lower()
    s = re.sub(r"[^a-z0-9\s-]+", '', s)
    s = re.sub(r"\s+", '-', s.strip())
    s = re.sub(r"-+", '-', s).strip('-')
    return s


def norm_addr_key(street, city, state):
    """Normalize street address for dedup. Lowercases, strips suite/ste tokens,
    collapses whitespace and punctuation. The goal is collisions for 'same
    building' not string equality."""
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
    if len(digits) != 10:
        return None
    return digits


def format_phone(raw):
    d = norm_phone(raw)
    if not d:
        return raw
    return f'({d[0:3]}) {d[3:6]}-{d[6:10]}'


# ---- chain fetchers -------------------------------------------------------

def fetch_gameday():
    """GameDay publishes a Yext-backed entities feed."""
    url = 'https://api.gamedaymenshealth.com/entities'
    data = json.loads(http_get(url))
    out = []
    for e in data.get('data', []):
        if e.get('countryCode') != 'US':
            continue
        state_code = e.get('stateCode')
        if state_code not in US_STATES:
            continue
        name = e.get('name') or e.get('locationFinderName') or ''
        name = unescape(name).replace('\u2019', "'").strip()
        street = (e.get('address') or '').strip()
        full_addr_parts = [street, e.get('city'), f'{state_code} {e.get("zip") or ""}'.strip()]
        full_addr = ', '.join(p for p in full_addr_parts if p)
        hours_map = e.get('formattedHours') or {}
        hours = []
        for day in ('monday','tuesday','wednesday','thursday','friday','saturday','sunday'):
            v = hours_map.get(day)
            if v:
                hours.append(f'{day.capitalize()}: {v}')
        out.append({
            'chainId': e.get('id'),
            'name': name,
            'street': street,
            'city': e.get('city'),
            'state': state_code,
            'zip': e.get('zip'),
            'lat': e.get('latitude'),
            'lng': e.get('longitude'),
            'phone': e.get('mainPhoneDisp') or e.get('mainPhone'),
            'website': e.get('url'),
            'address': full_addr,
            'hours': hours,
            'rating': e.get('rating'),
            'ratingCount': None,
        })
    return out


def fetch_lowtcenter():
    """Low T Center / SynergenX — inline JSON on the /locations/ page."""
    url = 'https://www.lowtcenter.com/locations/'
    html = http_get(url)
    # The page inlines a long array of clinic objects. Extract each well-formed
    # object individually; a bracket walk across the whole page has too much noise.
    pattern = re.compile(
        r'\{"name":"(?P<name>[^"]*)",'
        r'"address_part_one":"(?P<a1>[^"]*)",'
        r'"address_part_two":"(?P<a2>[^"]*)",'
        r'"city":"(?P<city>[^"]*)",'
        r'"state":"(?P<state>[^"]*)",'
        r'"zipcode":"(?P<zip>[^"]*)",'
        r'"phone_number":"(?P<phone>[^"]*)",'
        r'"coming_soon":(?P<coming>true|false),'
        r'"opening_day":"[^"]*",'
        r'(?P<hours>(?:"[a-z]+_hours":"[^"]*",?\s*){0,7})'
        r'"url":"(?P<url>[^"]*)",'
        r'"latitude":"(?P<lat>[^"]*)",'
        r'"longitude":"(?P<lng>[^"]*)"\}'
    )
    out = []
    for m in pattern.finditer(html):
        if m.group('coming') == 'true':
            continue
        state = m.group('state').strip().upper()
        if state not in US_STATES:
            continue
        name = unescape(m.group('name')).replace('\u2019', "'")
        street = (m.group('a1') or '').strip()
        a2 = (m.group('a2') or '').strip()
        if a2:
            street = f'{street} {a2}'.strip()
        # Parse hours — clinic JSON has monday_hours..sunday_hours
        hours = []
        for day in ('monday','tuesday','wednesday','thursday','friday','saturday','sunday'):
            hm = re.search(rf'"{day}_hours":"([^"]*)"', m.group(0))
            if hm and hm.group(1):
                hours.append(f'{day.capitalize()}: {hm.group(1)}')
        url_raw = m.group('url').replace('\\/', '/')
        lat = m.group('lat'); lng = m.group('lng')
        try: lat = float(lat)
        except: lat = None
        try: lng = float(lng)
        except: lng = None
        # Synthetic id — stable across runs
        chain_id = slugify(f"{m.group('city')}-{street[:20]}")
        out.append({
            'chainId': chain_id,
            'name': name,
            'street': street,
            'city': m.group('city'),
            'state': state,
            'zip': m.group('zip'),
            'lat': lat,
            'lng': lng,
            'phone': m.group('phone'),
            'website': url_raw,
            'address': f"{street}, {m.group('city')}, {state} {m.group('zip')}",
            'hours': hours,
            'rating': None,
            'ratingCount': None,
        })
    return out


def fetch_renewvit():
    """Renew Vitality — window.locations JS array on vitalityhrt.com/location/."""
    url = 'https://www.vitalityhrt.com/location/'
    html = http_get(url)
    m = re.search(r'window\.locations\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not m:
        raise RuntimeError('renewvit: window.locations not found')
    raw = m.group(1)
    data = json.loads(raw)
    seen_addr = set()  # dedupe in-chain (TRT + Peptide duplicate the same clinic)
    out = []
    for e in data:
        addr = (e.get('address') or '').strip()
        if not addr or re.search(r'service\s*area', addr, re.I):
            continue
        # Parse city, state, zip out of the trailing address components
        am = re.search(r',\s*([A-Za-z .\-]+?),?\s+([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$', addr)
        if not am:
            continue
        city = am.group(1).strip().rstrip(',')
        state = am.group(2)
        zip_code = am.group(3)
        if state not in US_STATES:
            continue
        # Street = everything before ", <city>, <state> <zip>"
        street = addr[:am.start()].rstrip(', ').strip()
        key = norm_addr_key(street, city, state)
        if key in seen_addr:
            continue
        seen_addr.add(key)
        phone = e.get('phone') or ''
        lat = e.get('lat'); lng = e.get('lng')
        try: lat = round(float(lat), 6)
        except: lat = None
        try: lng = round(float(lng), 6)
        except: lng = None
        # Compose clinic name — canonical "Renew Vitality Testosterone Clinic of {City}"
        name = f'Renew Vitality Testosterone Clinic of {city}'
        chain_id = slugify(f'{city}-{state}-{street[:20]}')
        out.append({
            'chainId': chain_id,
            'name': name,
            'street': street,
            'city': city,
            'state': state,
            'zip': zip_code,
            'lat': lat,
            'lng': lng,
            'phone': phone,
            'website': (e.get('permalink') or '').strip(),
            'address': addr,
            'hours': [],
            'rating': None,
            'ratingCount': None,
        })
    return out


def fetch_restore():
    """Restore Hyper Wellness — per-location pages enumerated via sitemap.xml;
    each page exposes a schema.org HealthAndBeautyBusiness ld+json block with
    full address, geo, phone, rating, reviewCount, and services."""
    sm_url = 'https://www.restore.com/sitemap.xml'
    sm = http_get(sm_url)
    urls = sorted(set(re.findall(
        r'<loc>(https://www\.restore\.com/locations/[^<]+)</loc>', sm
    )))
    print(f'  restore: sitemap → {len(urls)} location URLs')
    out = []
    for i, u in enumerate(urls, 1):
        try:
            html = http_get(u)
        except Exception as e:
            print(f'    [err] {u}: {e}')
            time.sleep(1)
            continue
        # Extract ld+json block
        mblocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.S
        )
        data = None
        for b in mblocks:
            try:
                d = json.loads(b)
                if isinstance(d, dict) and d.get('@type') in ('HealthAndBeautyBusiness', 'LocalBusiness', 'MedicalBusiness'):
                    data = d; break
            except Exception:
                continue
        if not data:
            time.sleep(1.2)
            continue
        addr = data.get('address') or {}
        geo = data.get('geo') or {}
        agg = data.get('aggregateRating') or {}
        state_code = (addr.get('addressRegion') or '').upper()
        if state_code not in US_STATES:
            time.sleep(1.2)
            continue
        street = addr.get('streetAddress') or ''
        city = addr.get('addressLocality') or ''
        zip_code = addr.get('postalCode') or ''
        phone = data.get('telephone') or ''
        try: rating = float(agg['ratingValue']) if agg.get('ratingValue') else None
        except: rating = None
        try: rc = int(agg['reviewCount']) if agg.get('reviewCount') else None
        except: rc = None
        hours = data.get('openingHours') or []
        if isinstance(hours, str):
            hours = [hours]
        # Restore encodes Mo-Su 2-letter codes; leave as-is (we display as-provided)
        services = []
        for offer in data.get('makesOffer') or []:
            n = offer.get('name') if isinstance(offer, dict) else None
            if n: services.append(n)
        # Slug from URL tail ('ar-rogers-ar002' → chainId)
        chain_id = u.rstrip('/').rsplit('/', 1)[-1]
        name = data.get('name') or f'Restore Hyper Wellness — {city}, {state_code}'
        out.append({
            'chainId': chain_id,
            'name': name,
            'street': street,
            'city': city,
            'state': state_code,
            'zip': zip_code,
            'lat': geo.get('latitude'),
            'lng': geo.get('longitude'),
            'phone': phone,
            'website': u,
            'address': f'{street}, {city}, {state_code} {zip_code}'.strip(', '),
            'hours': hours,
            'rating': rating,
            'ratingCount': rc,
            'services': services,
        })
        time.sleep(1.2)  # polite crawl
        if i % 40 == 0:
            print(f'    restore progress: {i}/{len(urls)} ({len(out)} parsed)')
    return out


def fetch_serotonin():
    """Serotonin Centers — the /locations/ index inlines a DatoCMS locations
    array. Coordinates, phone, slug are structured; address is HTML fragment
    we need to parse."""
    url = 'https://www.serotonincenters.com/locations/'
    html = http_get(url)
    idx = html.find('"locations":[')
    if idx < 0:
        raise RuntimeError('serotonin: locations array not found')
    start = idx + len('"locations":')
    depth, in_str, esc, end = 0, False, False, None
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc: esc = False
            elif ch == '\\': esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == '[': depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    end = i + 1; break
    if end is None:
        raise RuntimeError('serotonin: unterminated locations array')
    data = json.loads(html[start:end])
    out = []
    for e in data:
        if e.get('inactive') or e.get('comingSoon'):
            continue
        # address field is HTML-wrapped: "<p>7790 Winter Garden Vineland Rd, Suite 100<br />Windermere, FL 34786</p>"
        addr_html = e.get('address') or ''
        # Strip tags, normalize whitespace
        addr_text = re.sub(r'<[^>]+>', ' ', addr_html)
        addr_text = re.sub(r'\s+', ' ', unescape(addr_text)).strip().rstrip(',')
        # Normalize "street, city, STATE zip" — some encode as "street<br>city, ST zip" giving "street city, ST zip"
        # Find ", STATE ZIP" tail
        tail = re.search(r'([A-Za-z][A-Za-z\s\.\-]+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$', addr_text)
        if not tail:
            continue
        city = tail.group(1).strip().rstrip(',')
        state = tail.group(2)
        zip_code = tail.group(3)
        if state not in US_STATES:
            continue
        head = addr_text[:tail.start()].rstrip(', ').strip()
        # If head still contains the city (because address was "st city, ST zip"), try to split
        street = head
        coords = e.get('coordinates') or {}
        lat = coords.get('latitude') if isinstance(coords, dict) else None
        lng = coords.get('longitude') if isinstance(coords, dict) else None
        phone = e.get('phoneNumber') or e.get('altTrackingNumber') or ''
        slug = e.get('slug') or str(e.get('id') or '')
        out.append({
            'chainId': slug,
            'name': f'Serotonin Centers — {city}, {state}',
            'street': street,
            'city': city,
            'state': state,
            'zip': zip_code,
            'lat': lat,
            'lng': lng,
            'phone': phone,
            'website': f'https://www.serotonincenters.com/locations/{slug}/' if slug else None,
            'address': addr_text,
            'hours': [],
            'rating': e.get('reviewsRating'),
            'ratingCount': e.get('reviewsCount'),
        })
    return out


# Each chain has one or more "match tokens" — lowercase substrings that, when
# found in an existing record's name, identify it as a member of this chain.
# Used for name-based dedup when street-level address match fails (Google
# Places often returns no street component, only city).
CHAINS = {
    'gameday':    ('Gameday Men\u2019s Health', 'chain-gameday',
                   ['gameday'], fetch_gameday),
    'lowt':       ('Low T Center',              'chain-lowt',
                   ['low t center', 'synergenx'], fetch_lowtcenter),
    'renewvit':   ('Renew Vitality',            'chain-renewvit',
                   ['renew vitality'], fetch_renewvit),
    'restore':    ('Restore Hyper Wellness',    'chain-restore',
                   ['restore hyper wellness', 'restore hyperwellness'], fetch_restore),
    'serotonin':  ('Serotonin Centers',         'chain-serotonin',
                   ['serotonin centers', 'serotonin+'], fetch_serotonin),
}


# ---- merge into clinics.min.json ------------------------------------------

def build_indices(records, chain_token_map):
    """Build indices for dedup:

    - by_addr: normalized street+city+state key → record (exact building match)
    - by_phone: 10-digit phone → first record with that phone
    - by_chain_city: (chain_key, citySlug, state) → list of records whose name
      matches that chain's tokens. Used as fallback when the Places record
      lacks a street address.
    """
    by_addr = {}
    by_phone = {}
    by_chain_city = {}
    for r in records:
        ak = norm_addr_key(r.get('street'), r.get('city'), r.get('state'))
        if r.get('street') and ak and ak != '||':
            by_addr[ak] = r
        pd = norm_phone(r.get('phone'))
        if pd:
            by_phone.setdefault(pd, r)
        name_lc = (r.get('name') or '').lower()
        for chain_key, tokens in chain_token_map.items():
            if any(t in name_lc for t in tokens):
                k = (chain_key, slugify(r.get('city') or ''), (r.get('state') or '').upper())
                by_chain_city.setdefault(k, []).append(r)
                break
    return by_addr, by_phone, by_chain_city


def to_clinic_record(raw, chain_key, chain_display_name, chain_prefix):
    state = raw['state']
    state_name = US_STATES.get(state)
    phone_fmt = format_phone(raw.get('phone')) if raw.get('phone') else None
    return {
        'placeId': f"{chain_prefix}-{raw['chainId']}",
        'name': raw['name'],
        'slug': slugify(raw['name']) or raw['chainId'],
        'address': raw.get('address'),
        'street': raw.get('street'),
        'city': raw.get('city'),
        'citySlug': slugify(raw.get('city') or '') or None,
        'state': state,
        'stateSlug': slugify(state_name) if state_name else None,
        'zip': raw.get('zip'),
        'lat': raw.get('lat'),
        'lng': raw.get('lng'),
        'phone': phone_fmt,
        'website': raw.get('website') or None,
        'rating': raw.get('rating'),
        'ratingCount': raw.get('ratingCount'),
        'priceLevel': None,
        'googleUrl': None,
        'hours': raw.get('hours') or [],
        'services': raw.get('services') or ['TRT'],
        'types': ['medical_clinic'],
        'verified': False,
        'featured': False,
        'telehealth': False,
        'source': f'chain:{chain_key}',
        'chain': chain_display_name,
        'lastSeenAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chains', type=str, default='', help='comma-sep chain keys; default = all')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    wanted = [c.strip() for c in args.chains.split(',') if c.strip()] or list(CHAINS.keys())
    bad = [w for w in wanted if w not in CHAINS]
    if bad:
        print(f'[err] unknown chains: {bad} (known: {list(CHAINS.keys())})'); sys.exit(2)

    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            existing = json.load(f)
        if not isinstance(existing, list):
            existing = existing.get('clinics', [])
    else:
        existing = []
    print(f'[start] loaded {len(existing)} existing clinics')

    chain_token_map = {k: v[2] for k, v in CHAINS.items()}
    by_addr, by_phone, by_chain_city = build_indices(existing, chain_token_map)
    by_place = {r['placeId']: r for r in existing if r.get('placeId')}

    added = 0
    per_chain = {}

    for key in wanted:
        display, prefix, _tokens, fetcher = CHAINS[key]
        print(f'\n[chain] {key} ({display})')
        try:
            raws = fetcher()
        except Exception as e:
            print(f'[err] {key} fetch: {e}')
            continue
        print(f'  fetched {len(raws)} raw locations')

        ch_added = 0
        ch_skip_addr = 0
        ch_skip_name_city = 0
        ch_skip_phone = 0
        ch_enriched = 0  # existing records we filled in missing street/lat/lng from chain data

        for raw in raws:
            ak = norm_addr_key(raw.get('street'), raw.get('city'), raw.get('state'))
            pd = norm_phone(raw.get('phone'))
            match = None
            how = None

            # Priority 1: exact street+city+state match
            if raw.get('street') and ak and ak != '||' and ak in by_addr:
                match = by_addr[ak]; how = 'addr'; ch_skip_addr += 1

            # Priority 2: chain token + city + state (catches Places records without street).
            # When multiple candidates exist (e.g. 5 Gameday locations in LA), prefer one whose
            # street matches; otherwise consume candidates in order so each incoming record
            # pairs with a distinct existing record instead of all collapsing onto candidates[0].
            if not match:
                city_key = (key, slugify(raw.get('city') or ''), raw.get('state'))
                candidates = by_chain_city.get(city_key, [])
                if candidates:
                    best_idx = 0
                    if raw.get('street'):
                        raw_token = re.match(r'\d+', raw['street'])
                        if raw_token:
                            for i, c in enumerate(candidates):
                                cs = c.get('street') or ''
                                if cs.lower().startswith(raw_token.group(0) + ' '):
                                    best_idx = i; break
                    match = candidates.pop(best_idx)
                    how = 'chain+city'; ch_skip_name_city += 1

            # Priority 3: phone match, but require same city+state to avoid call-center collisions
            if not match and pd:
                cand = by_phone.get(pd)
                if cand and slugify(cand.get('city') or '') == slugify(raw.get('city') or '') \
                   and (cand.get('state') or '') == raw.get('state'):
                    match = cand; how = 'phone'; ch_skip_phone += 1

            if match:
                # Tag as chain member, and opportunistically fill gaps from the chain record
                # (Google Places often lacks street/lat/lng — chain data has them).
                if not match.get('chain'):
                    match['chain'] = display
                filled_any = False
                for field in ('street', 'zip', 'lat', 'lng'):
                    if not match.get(field) and raw.get(field):
                        match[field] = raw[field]; filled_any = True
                if filled_any:
                    ch_enriched += 1
                continue

            rec = to_clinic_record(raw, key, display, prefix)
            if rec['placeId'] in by_place:
                by_place[rec['placeId']].update({
                    'lastSeenAt': rec['lastSeenAt'],
                    'hours': rec['hours'] or by_place[rec['placeId']].get('hours'),
                })
                continue
            existing.append(rec)
            by_place[rec['placeId']] = rec
            if ak and ak != '||':
                by_addr[ak] = rec
            if pd:
                by_phone.setdefault(pd, rec)
            by_chain_city.setdefault((key, slugify(rec.get('city') or ''), rec.get('state')), []).append(rec)
            ch_added += 1

        per_chain[key] = {
            'raw': len(raws), 'added': ch_added,
            'dedup_addr': ch_skip_addr, 'dedup_chain_city': ch_skip_name_city,
            'dedup_phone': ch_skip_phone, 'enriched': ch_enriched,
        }
        added += ch_added
        print(f'  +{ch_added} new | dedup: addr={ch_skip_addr} chain+city={ch_skip_name_city} '
              f'phone={ch_skip_phone} | enriched existing: {ch_enriched}')

    print(f'\n[summary] total new across chains: {added}')
    for k, stats in per_chain.items():
        print(f'  {k}: {stats}')

    if args.dry_run:
        print('[dry-run] skipping save')
        return

    existing.sort(key=lambda c: (c.get('stateSlug') or '', c.get('citySlug') or '', c.get('name') or ''))
    with open(DATA_FILE, 'w') as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    print(f'[save] wrote {len(existing)} clinics → {DATA_FILE}')


if __name__ == '__main__':
    main()
