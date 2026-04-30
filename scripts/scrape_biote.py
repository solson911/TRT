#!/usr/bin/env python3
"""
scrape_biote.py — enrich + discover clinics via the Biote certified-provider
directory (biote.com/biote-providers/<state>/<city>).

Biote is a large BHRT certification network (6,400+ providers across ~50
states). Their locator lists provider addresses by state → city; individual
provider detail pages contain the practitioner name.

Strategy (two-phase to keep quality high and HTTP cost bounded):

  Phase 1: walk state index → city index pages. Collect (state, city, street,
           phone, detail_url) tuples only. No detail pages yet.

  Phase 2: for each tuple, try to match against existing clinics.min.json by
           (street + city + state), (phone + city + state), or chain-token
           fallback. Matched records get tagged with `biote: true` and
           `chain: "Biote Certified"` — this is the most valuable output
           (adding a certification signal to clinics we already list).

  Phase 3: for unmatched tuples only, fetch the detail page to extract the
           practitioner name, then add as a new record with the practitioner
           as the clinic name. This is the net-new yield.

Polite crawl: 1.5s delay between requests, realistic User-Agent. Checkpoints
to a progress file after every 200 HTTP calls so a crash or overnight
timeout is resumable.

Usage:
  python3 scripts/scrape_biote.py                      # full run
  python3 scripts/scrape_biote.py --states TX,CA       # pilot
  python3 scripts/scrape_biote.py --no-details         # skip phase 3
  python3 scripts/scrape_biote.py --resume             # continue from checkpoint
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from html import unescape

sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.clinics_io import load_all, save_all  # noqa: E402
CHECKPOINT = os.path.join(ROOT, 'logs', 'biote_progress.json')

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}
POLITE_DELAY_S = 1.5

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
    'DC':'District of Columbia',
}
STATE_SLUG_TO_ABBR = {v.lower().replace(' ', '-'): k for k, v in US_STATES.items()}
STATE_SLUG_TO_ABBR['district-of-columbia'] = 'DC'


def http_get(url, timeout=25, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))


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


def format_phone(raw):
    d = norm_phone(raw)
    if not d:
        return raw
    return f'({d[0:3]}) {d[3:6]}-{d[6:10]}'


# ---- Biote pagewalk -------------------------------------------------------

def fetch_state_index(state_slug):
    """Returns list of city slugs for a US state."""
    url = f'https://biote.com/biote-providers/{state_slug}'
    html = http_get(url)
    # City links look like: /biote-providers/<state>/<city-slug>
    cities = sorted(set(re.findall(
        rf'/biote-providers/{re.escape(state_slug)}/([a-z0-9-]+)(?=["/])',
        html
    )))
    return cities


def parse_city_page(state_slug, city_slug, html):
    """Extract provider records from a city page.

    Each entry in the page is rendered as:
      <h...>Biote Provider</h...>
      <...>10425 Huffmeister Suite 230, Houston, TX 77065</...>
      <a href="tel:281-921-1890">281-921-1890</a>
      <a href="https://biote.com/bioidentical-...">VIEW PROVIDER PAGE</a>

    We anchor on the detail-page URL (unique per entry) and walk
    backwards/forwards in the HTML chunk for the paired address + phone.
    """
    out = []
    state = STATE_SLUG_TO_ABBR.get(state_slug)
    if not state:
        return out
    detail_pat = re.compile(
        rf'href="(https://biote\.com/bioidentical-hormone-replacement-therapy-provider/{re.escape(state_slug)}/{re.escape(city_slug)}/[^"]+)"',
        re.I,
    )
    # Pair each detail url with its nearest preceding address + tel: anchor.
    matches = list(detail_pat.finditer(html))
    for i, m in enumerate(matches):
        detail_url = m.group(1)
        # Search a window ending at this match for address + phone
        window_start = matches[i-1].end() if i > 0 else max(0, m.start() - 4000)
        window = html[window_start:m.start()]
        # Address: look for "<digits> ... , <City>, <STATE> <ZIP>"
        addr_match = re.search(
            rf'(\d[\d\w\s\.\-,#/]+?,\s*[A-Za-z][A-Za-z\s\.\-]*?,\s*{state}\s+\d{{5}}(?:-\d{{4}})?)',
            window,
        )
        if not addr_match:
            continue
        full_addr = unescape(addr_match.group(1).strip())
        # Parse: street, city, STATE ZIP
        parts = re.match(
            rf'^(.+?),\s*([A-Za-z][A-Za-z\s\.\-]+?),\s*{state}\s+(\d{{5}})',
            full_addr,
        )
        if not parts:
            continue
        street = parts.group(1).strip()
        city = parts.group(2).strip()
        zip_code = parts.group(3)
        # Phone: tel: link after the address
        phone_match = re.search(r'href="tel:([\d\-\(\)\s\+]+)"', window)
        phone = phone_match.group(1).strip() if phone_match else None
        slug_id = detail_url.rstrip('/').split('/')[-1]
        out.append({
            'chainId': slug_id,
            'detailUrl': detail_url,
            'street': street,
            'city': city,
            'state': state,
            'zip': zip_code,
            'phone': phone,
            'address': full_addr,
        })
    return out


def fetch_provider_name(detail_url):
    """Extract the practitioner name from a provider detail page.

    Detail pages render the name in an h1 or prominent heading. We look for
    patterns like 'First Last, MD' or 'First M. Last, DO' — common credential
    suffixes are MD, DO, NP, PA, NP-C, DNP, APRN, FNP, PhD. If no credentialed
    name is found, returns None (caller falls back to generic name).
    """
    try:
        html = http_get(detail_url)
    except Exception:
        return None
    # Strip tags for a text soup, then look for name patterns.
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', unescape(text))
    creds = r'(?:MD|DO|NP(?:-C)?|PA(?:-C)?|DNP|APRN|FNP(?:-BC|-C)?|PhD|PharmD|NMD|RN|ND)'
    # "First [Middle] Last, MD" style
    m = re.search(rf'\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-zA-Z\'\-]+){{1,3}}),\s*{creds}\b', text)
    if m:
        return m.group(0).strip()
    return None


# ---- merge into clinics.min.json ------------------------------------------

def load_clinics():
    return load_all()


def save_clinics(records):
    save_all(records)


def build_indices(records):
    by_addr = {}
    by_phone = {}
    by_place = {}
    for r in records:
        ak = norm_addr_key(r.get('street'), r.get('city'), r.get('state'))
        if r.get('street') and ak and ak != '||':
            by_addr[ak] = r
        pd = norm_phone(r.get('phone'))
        if pd:
            key = (pd, slugify(r.get('city') or ''), (r.get('state') or '').upper())
            by_phone.setdefault(key, r)
        if r.get('placeId'):
            by_place[r['placeId']] = r
    return by_addr, by_phone, by_place


def to_new_record(raw, name):
    state = raw['state']
    state_name = US_STATES.get(state)
    phone_fmt = format_phone(raw.get('phone')) if raw.get('phone') else None
    return {
        'placeId': f"chain-biote-{raw['chainId']}",
        'name': name,
        'slug': slugify(name) or raw['chainId'],
        'address': raw.get('address'),
        'street': raw.get('street'),
        'city': raw.get('city'),
        'citySlug': slugify(raw.get('city') or '') or None,
        'state': state,
        'stateSlug': slugify(state_name) if state_name else None,
        'zip': raw.get('zip'),
        'lat': None,
        'lng': None,
        'phone': phone_fmt,
        'website': raw.get('detailUrl'),
        'rating': None,
        'ratingCount': None,
        'priceLevel': None,
        'googleUrl': None,
        'hours': [],
        'services': ['TRT', 'BHRT', 'HRT'],
        'types': ['medical_clinic'],
        'verified': False,
        'featured': False,
        'telehealth': False,
        'source': 'chain:biote',
        'chain': 'Biote Certified',
        'lastSeenAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def load_checkpoint():
    if not os.path.exists(CHECKPOINT):
        return {'seen_urls': [], 'all_tuples': [], 'completed_states': []}
    try:
        with open(CHECKPOINT) as f:
            return json.load(f)
    except Exception:
        return {'seen_urls': [], 'all_tuples': [], 'completed_states': []}


def save_checkpoint(state):
    os.makedirs(os.path.dirname(CHECKPOINT), exist_ok=True)
    with open(CHECKPOINT, 'w') as f:
        json.dump(state, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--states', type=str, default='', help='comma-sep US state abbrs')
    ap.add_argument('--resume', action='store_true', help='reuse phase 1 tuples from checkpoint')
    ap.add_argument('--no-details', action='store_true', help='skip phase 3 (detail-page fetches)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    target_states = [s.strip().upper() for s in args.states.split(',') if s.strip()] or list(US_STATES.keys())
    target_slugs = [US_STATES[s].lower().replace(' ', '-') for s in target_states if s in US_STATES]
    # DC not in US_STATES already added above
    if 'DC' in target_states and 'district-of-columbia' not in target_slugs:
        target_slugs.append('district-of-columbia')

    existing = load_clinics()
    print(f'[start] loaded {len(existing)} existing clinics')
    by_addr, by_phone, by_place = build_indices(existing)

    ckpt = load_checkpoint() if args.resume else {'seen_urls': [], 'all_tuples': [], 'completed_states': []}
    all_tuples = ckpt.get('all_tuples', [])
    completed = set(ckpt.get('completed_states', []))

    # ----------- PHASE 1: walk state → city pages ------------
    req_count = 0
    if not args.resume or not all_tuples:
        print(f'\n[phase 1] enumerating {len(target_slugs)} states')
        for st_slug in target_slugs:
            if st_slug in completed:
                print(f'  {st_slug}: already completed, skipping')
                continue
            try:
                cities = fetch_state_index(st_slug)
                req_count += 1
                time.sleep(POLITE_DELAY_S)
                print(f'  {st_slug}: {len(cities)} cities')
            except Exception as e:
                print(f'  {st_slug}: [err] {e}')
                continue
            st_abbr = STATE_SLUG_TO_ABBR.get(st_slug)
            for city_slug in cities:
                url = f'https://biote.com/biote-providers/{st_slug}/{city_slug}'
                try:
                    html = http_get(url)
                    req_count += 1
                except Exception as e:
                    print(f'    {city_slug}: [err] {e}')
                    time.sleep(POLITE_DELAY_S)
                    continue
                time.sleep(POLITE_DELAY_S)
                entries = parse_city_page(st_slug, city_slug, html)
                # Dedupe by detail URL within phase 1
                for e in entries:
                    if e['detailUrl'] not in ckpt.get('seen_urls', []):
                        all_tuples.append(e)
                        ckpt.setdefault('seen_urls', []).append(e['detailUrl'])
                if req_count % 50 == 0:
                    ckpt['all_tuples'] = all_tuples
                    save_checkpoint(ckpt)
                    print(f'    [checkpoint] {req_count} reqs, {len(all_tuples)} tuples')
            completed.add(st_slug)
            ckpt['completed_states'] = sorted(completed)
            ckpt['all_tuples'] = all_tuples
            save_checkpoint(ckpt)

        ckpt['all_tuples'] = all_tuples
        save_checkpoint(ckpt)

    print(f'\n[phase 1 done] {len(all_tuples)} provider tuples from {len(completed)} states')

    # ----------- PHASE 2: match against existing ------------
    matched = 0
    unmatched = []
    print('\n[phase 2] matching tuples against existing clinics')
    for t in all_tuples:
        ak = norm_addr_key(t.get('street'), t.get('city'), t.get('state'))
        pd = norm_phone(t.get('phone'))
        hit = None
        if t.get('street') and ak in by_addr:
            hit = by_addr[ak]
        elif pd:
            key = (pd, slugify(t['city']), t['state'])
            if key in by_phone:
                hit = by_phone[key]
        if hit:
            if not hit.get('chain'):
                hit['chain'] = 'Biote Certified'
            elif 'Biote' not in hit.get('chain', ''):
                hit['chain'] = f"{hit['chain']}, Biote Certified"
            hit['biote'] = True
            # Also tag services
            svcs = hit.get('services') or []
            if 'BHRT' not in svcs:
                svcs.append('BHRT')
                hit['services'] = svcs
            matched += 1
        else:
            unmatched.append(t)
    print(f'  matched: {matched}')
    print(f'  unmatched (candidates for net-new): {len(unmatched)}')

    # ----------- PHASE 3: detail-page fetches for unmatched ----
    added = 0
    if not args.no_details and unmatched:
        print(f'\n[phase 3] fetching detail pages for {len(unmatched)} unmatched tuples')
        for i, t in enumerate(unmatched, 1):
            pid = f"chain-biote-{t['chainId']}"
            if pid in by_place:
                continue  # already there from prior run
            name = fetch_provider_name(t['detailUrl'])
            time.sleep(POLITE_DELAY_S)
            if not name:
                # fallback: use street address as name anchor
                name = f"Biote Certified Provider — {t['city']}, {t['state']}"
            rec = to_new_record(t, name)
            existing.append(rec)
            by_place[pid] = rec
            added += 1
            if i % 100 == 0:
                if not args.dry_run:
                    save_clinics(existing)
                print(f'  [{i}/{len(unmatched)}] added {added} so far')
    else:
        print('\n[phase 3] skipped (--no-details or no unmatched)')

    print(f'\n[summary]')
    print(f'  tuples fetched:          {len(all_tuples)}')
    print(f'  existing records tagged: {matched}')
    print(f'  net-new records added:   {added}')

    if args.dry_run:
        print('[dry-run] not saving')
        return

    save_clinics(existing)
    print(f'[save] wrote {len(existing)} clinics to per-state shards')


if __name__ == '__main__':
    main()
