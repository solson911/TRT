#!/usr/bin/env python3
"""
enrich_places_extras.py — backfill fields we skipped on the initial Places ingest.

For each clinic that was sourced from google-places, call Place Details (GET
/v1/places/{id}) with a field mask that pulls the "free at same SKU" extras
we didn't originally grab: photos, businessStatus, reviews, editorialSummary,
generativeSummary, primaryType/primaryTypeDisplayName, accessibilityOptions,
paymentOptions, parkingOptions, shortFormattedAddress.

Usage:
  python3 scripts/enrich_places_extras.py --pilot            # first 5, dry-run report
  python3 scripts/enrich_places_extras.py --pilot --save     # first 5, write to data
  python3 scripts/enrich_places_extras.py                    # full run, all clinics
  python3 scripts/enrich_places_extras.py --limit 100        # capped run
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'data', 'clinics.min.json')

API_KEY = (
    os.environ.get('PLACES_UNRESTRICTED_API_KEY')
    or os.environ.get('GOOGLE_PLACES_API_KEY')
    or 'AIzaSyCIrnX_thibzANXFSbkyu04cFWoWMjK718'
)
DETAILS_URL = 'https://places.googleapis.com/v1/places/{place_id}'

SAVE_EVERY = 100

FIELD_MASK = ','.join([
    'id',
    'shortFormattedAddress',
    'primaryType',
    'primaryTypeDisplayName',
    'businessStatus',
    'photos',
    'editorialSummary',
    'generativeSummary',
    'reviews',
    'accessibilityOptions',
    'paymentOptions',
    'parkingOptions',
    'priceRange',
])


def http_get(url, headers, timeout=20):
    req = urllib.request.Request(url, headers=headers, method='GET')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_details(place_id):
    url = DETAILS_URL.format(place_id=urllib.parse.quote(place_id, safe=''))
    headers = {
        'X-Goog-Api-Key': API_KEY,
        'X-Goog-FieldMask': FIELD_MASK,
    }
    return http_get(url, headers)


def slim_photos(photos):
    """Keep name + dimensions + author attribution, drop nothing else."""
    out = []
    for p in (photos or [])[:10]:
        out.append({
            'name': p.get('name'),
            'widthPx': p.get('widthPx'),
            'heightPx': p.get('heightPx'),
            'authorAttributions': p.get('authorAttributions') or [],
        })
    return out


def slim_reviews(reviews):
    """Keep author / rating / text / time / relative so we can render and re-refresh."""
    out = []
    for r in (reviews or [])[:5]:
        author = r.get('authorAttribution') or {}
        text = r.get('text') or {}
        out.append({
            'name': r.get('name'),
            'rating': r.get('rating'),
            'text': text.get('text'),
            'textLang': text.get('languageCode'),
            'relativePublishTimeDescription': r.get('relativePublishTimeDescription'),
            'publishTime': r.get('publishTime'),
            'authorName': author.get('displayName'),
            'authorUri': author.get('uri'),
            'authorPhotoUri': author.get('photoUri'),
        })
    return out


def slim_summary(s):
    """Handle both editorialSummary (LocalizedText: {text, languageCode}) and
    generativeSummary ({overview: {text, languageCode}, ...}).
    """
    if not s or not isinstance(s, dict):
        return None
    text = None
    lang = None
    # generativeSummary: wrapped under "overview"
    if 'overview' in s and isinstance(s['overview'], dict):
        text = s['overview'].get('text')
        lang = s['overview'].get('languageCode')
    # editorialSummary: LocalizedText at top level
    elif 'text' in s:
        val = s['text']
        if isinstance(val, str):
            text = val
            lang = s.get('languageCode')
        elif isinstance(val, dict):
            text = val.get('text')
            lang = val.get('languageCode')
    if not text:
        return None
    return {'text': text, 'languageCode': lang}


def merge_extras(clinic, details):
    """Mutate clinic in-place with extras from Place Details response."""
    if 'primaryType' in details:
        clinic['primaryType'] = details['primaryType']
    if 'primaryTypeDisplayName' in details:
        dn = details['primaryTypeDisplayName']
        clinic['primaryTypeDisplay'] = dn.get('text') if isinstance(dn, dict) else dn
    if 'shortFormattedAddress' in details:
        clinic['addressShort'] = details['shortFormattedAddress']
    if 'businessStatus' in details:
        clinic['businessStatus'] = details['businessStatus']
    if 'photos' in details:
        clinic['photos'] = slim_photos(details['photos'])
    if 'editorialSummary' in details:
        clinic['editorialSummary'] = slim_summary(details['editorialSummary'])
    if 'generativeSummary' in details:
        clinic['generativeSummary'] = slim_summary(details['generativeSummary'])
    if 'reviews' in details:
        clinic['reviews'] = slim_reviews(details['reviews'])
    if 'accessibilityOptions' in details:
        clinic['accessibility'] = details['accessibilityOptions']
    if 'paymentOptions' in details:
        clinic['payment'] = details['paymentOptions']
    if 'parkingOptions' in details:
        clinic['parking'] = details['parkingOptions']
    if 'priceRange' in details:
        clinic['priceRange'] = details['priceRange']
    clinic['extrasEnrichedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    return clinic


def load_clinics():
    with open(DATA_FILE) as f:
        return json.load(f)


def save_clinics(clinics):
    with open(DATA_FILE, 'w') as f:
        json.dump(clinics, f, ensure_ascii=False, separators=(',', ':'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pilot', action='store_true', help='Only fetch first 5 for inspection')
    ap.add_argument('--save', action='store_true', help='Persist to disk (pilot defaults to no-save report)')
    ap.add_argument('--limit', type=int, default=0, help='Max number of clinics to enrich')
    ap.add_argument('--force', action='store_true', help='Re-enrich clinics that already have extrasEnrichedAt')
    args = ap.parse_args()

    if args.pilot and args.limit == 0:
        args.limit = 5

    clinics = load_clinics()
    DIRECTORY_CLASSES = {'primary_trt', 'offers_trt'}
    targets = [
        c for c in clinics
        if c.get('source') == 'google-places'
        and c.get('placeId')
        and c.get('classification') in DIRECTORY_CLASSES
        and (args.force or not c.get('extrasEnrichedAt'))
    ]
    if args.limit:
        targets = targets[:args.limit]

    print(f'[start] {len(targets)} clinics to enrich (of {len(clinics)} total)')
    if args.pilot:
        print('[pilot] dry-report mode — no data written unless --save')

    by_id = {c['placeId']: c for c in clinics if c.get('placeId')}

    success = 0
    failed = 0
    for i, clinic in enumerate(targets, 1):
        pid = clinic['placeId']
        try:
            details = fetch_details(pid)
        except Exception as e:
            print(f'[err] {pid} ({clinic.get("name")}): {e}')
            failed += 1
            continue
        merge_extras(clinic, details)
        by_id[pid] = clinic
        success += 1

        if args.pilot:
            photo_n = len(clinic.get('photos') or [])
            review_n = len(clinic.get('reviews') or [])
            has_es = bool(clinic.get('editorialSummary'))
            has_gs = bool(clinic.get('generativeSummary'))
            status = clinic.get('businessStatus', '?')
            ptype = clinic.get('primaryTypeDisplay') or clinic.get('primaryType') or '?'
            print(f'[{i}] {clinic.get("name")[:50]!r:52} status={status:25} type={ptype:35} photos={photo_n:2} reviews={review_n} editorialSummary={has_es} generativeSummary={has_gs}')

        if (args.save or not args.pilot) and i % SAVE_EVERY == 0:
            ordered = sorted(by_id.values(), key=lambda c: (c.get('stateSlug') or '', c.get('citySlug') or '', c.get('name') or ''))
            save_clinics(ordered)
            print(f'[checkpoint] {i}/{len(targets)} enriched, wrote to disk')

        time.sleep(0.05)  # be polite

    if args.save or not args.pilot:
        ordered = sorted(by_id.values(), key=lambda c: (c.get('stateSlug') or '', c.get('citySlug') or '', c.get('name') or ''))
        save_clinics(ordered)
        print(f'[done] wrote {len(ordered)} clinics → {DATA_FILE}')

    print(f'[summary] success={success} failed={failed}')


if __name__ == '__main__':
    main()
