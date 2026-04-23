#!/usr/bin/env python3
"""
Fetch a 64px favicon for every clinic in data/clinics.min.json via Google's
S2 favicon service and save to public/img/clinics/{placeId}.png. Resumable:
skips any clinic that already has a non-empty file unless --force is given.

Usage:
  python3 scripts/fetch_clinic_favicons.py                 # all clinics
  python3 scripts/fetch_clinic_favicons.py --state arizona
  python3 scripts/fetch_clinic_favicons.py --city scottsdale
  python3 scripts/fetch_clinic_favicons.py --limit 100
  python3 scripts/fetch_clinic_favicons.py --force
"""
import argparse, json, os, sys, time
import urllib.parse, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'clinics.min.json')
OUT_DIR = os.path.join(ROOT, 'public', 'img', 'clinics')
os.makedirs(OUT_DIR, exist_ok=True)

UA = "TRTIndexBot/0.1 (+https://trtindex.com)"
DIRECTORY_CLASSES = {'primary_trt', 'offers_trt'}


def domain_of(url):
    if not url:
        return None
    try:
        p = urllib.parse.urlparse(url if '://' in url else 'http://' + url)
        host = (p.netloc or p.path or '').split('/')[0]
        if host.startswith('www.'):
            host = host[4:]
        return host.strip() or None
    except Exception:
        return None


def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        return None


def placeid_filename(place_id):
    # place_id contains no unsafe chars for filenames (ChIJ... or similar)
    # but sanitize anyway
    return place_id.replace('/', '_').replace(':', '_')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--state', help='filter by stateSlug')
    ap.add_argument('--city', help='filter by citySlug')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--size', type=int, default=64)
    ap.add_argument('--sleep', type=float, default=0.25)
    args = ap.parse_args()

    with open(DATA) as f:
        raw = json.load(f)
    clinics = raw if isinstance(raw, list) else raw.get('clinics', [])
    clinics = [c for c in clinics if c.get('classification') in DIRECTORY_CLASSES]
    if args.state:
        clinics = [c for c in clinics if c.get('stateSlug') == args.state]
    if args.city:
        clinics = [c for c in clinics if c.get('citySlug') == args.city]

    # Dedupe by domain: many chain locations share a website, so fetch each
    # unique domain once and then copy the bytes into per-placeId files.
    domain_to_bytes = {}
    fetched = skipped = failed = reused = 0
    processed = 0

    for c in clinics:
        if args.limit and processed >= args.limit:
            break
        pid = c.get('placeId')
        if not pid:
            continue
        out_path = os.path.join(OUT_DIR, f'{placeid_filename(pid)}.png')
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0 and not args.force:
            skipped += 1
            processed += 1
            continue
        domain = domain_of(c.get('website'))
        if not domain:
            failed += 1
            processed += 1
            continue

        if domain in domain_to_bytes:
            data = domain_to_bytes[domain]
            reused += 1
        else:
            url = f'https://www.google.com/s2/favicons?domain={domain}&sz={args.size}'
            data = fetch(url)
            domain_to_bytes[domain] = data
            if data and len(data) >= 200:
                fetched += 1
            time.sleep(args.sleep)

        if not data or len(data) < 200:
            failed += 1
            processed += 1
            continue
        with open(out_path, 'wb') as f:
            f.write(data)
        processed += 1
        if processed % 50 == 0:
            print(f'  ...{processed} processed (fetched={fetched} reused={reused} skipped={skipped} failed={failed})', file=sys.stderr)

    print(f'\ndone; total={processed} fetched={fetched} reused={reused} skipped={skipped} failed={failed}')


if __name__ == '__main__':
    main()
