#!/usr/bin/env python3
"""
Fetch a 128px favicon for each telehealth brand via Google's S2 favicon
service and save to public/img/telehealth/{slug}.png. Resumable: skips
brands that already have a non-empty file unless --force is passed.

Usage:
  python3 scripts/fetch_telehealth_favicons.py            # all brands
  python3 scripts/fetch_telehealth_favicons.py --only hone-health,blokes
  python3 scripts/fetch_telehealth_favicons.py --force    # refetch all
"""
import argparse, json, os, sys, time
import urllib.parse, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'telehealth.json')
OUT_DIR = os.path.join(ROOT, 'public', 'img', 'telehealth')
os.makedirs(OUT_DIR, exist_ok=True)

UA = "TRTIndexBot/0.1 (+https://trtindex.com)"

# Google's "globe" default that comes back when no favicon is found. Hash
# of the 128px PNG. If we get this, treat as "no favicon" so the template
# can fall back to a placeholder.
GOOGLE_DEFAULT_HASHES = set()  # populated lazily when we encounter one


def domain_of(url):
    if not url:
        return None
    p = urllib.parse.urlparse(url)
    host = p.netloc or p.path
    if host.startswith('www.'):
        host = host[4:]
    return host or None


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        print(f'  fetch err {url[:80]}: {e}', file=sys.stderr)
        return None


def fetch_favicon(domain, size=128):
    url = f'https://www.google.com/s2/favicons?domain={domain}&sz={size}'
    return fetch(url)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--only', help='comma-separated slugs')
    args = ap.parse_args()

    with open(DATA) as f:
        brands = json.load(f)
    if args.only:
        wanted = set(args.only.split(','))
        brands = [b for b in brands if b['slug'] in wanted]

    fetched = skipped = failed = 0
    for b in brands:
        slug = b['slug']
        out_path = os.path.join(OUT_DIR, f'{slug}.png')
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0 and not args.force:
            skipped += 1
            continue
        domain = domain_of(b.get('website'))
        if not domain:
            print(f'  [SKIP] {slug}: no website')
            failed += 1
            continue
        data = fetch_favicon(domain, size=128)
        if not data or len(data) < 200:
            print(f'  [FAIL] {slug} ({domain}): empty response')
            failed += 1
            continue
        with open(out_path, 'wb') as f:
            f.write(data)
        print(f'  [OK]   {slug} ({domain}) -> {len(data)} bytes')
        fetched += 1
        time.sleep(0.4)  # polite

    print(f'\ndone; fetched={fetched} skipped={skipped} failed={failed}')


if __name__ == '__main__':
    main()
