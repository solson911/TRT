#!/usr/bin/env python3
"""
scrape_telehealth.py - download homepage + common info pages for each seeded
telehealth brand. Raw HTML goes to data/telehealth-raw/{slug}/{page}.html
so later steps can extract structured data without re-hitting the network.

Tries a bunch of URL slugs that are likely to hold pricing/services/about/FAQ
copy. Silently skips 404s; counts successes per brand.

Resumable: if a slug already has >=3 non-empty HTML files saved, skip it.
Use --force to re-scrape everything.
"""
import argparse, json, os, random, sys, time
import urllib.request, urllib.error, urllib.parse
import socket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, 'data', 'telehealth-seed-validated.json')
RAW = os.path.join(ROOT, 'data', 'telehealth-raw')

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Paths we try on each brand's root. Keep short - we care about the copy that
# describes pricing, services, and clinical model.
CANDIDATE_PATHS = [
    '/',
    '/pricing', '/pricing/', '/price', '/plans', '/membership', '/cost',
    '/how-it-works', '/how-it-works/', '/process', '/getting-started',
    '/treatments', '/services', '/trt', '/testosterone', '/men',
    '/about', '/about/', '/our-team', '/team', '/providers', '/physicians', '/doctors',
    '/faq', '/faqs', '/frequently-asked-questions',
    '/reviews', '/testimonials',
]

os.makedirs(RAW, exist_ok=True)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            raw = r.read()
            # many sites return gzip despite us not asking; stdlib handles it
            if raw[:2] == b'\x1f\x8b':
                import gzip
                raw = gzip.decompress(raw)
            return raw.decode('utf-8', errors='replace')
    except (urllib.error.HTTPError, urllib.error.URLError,
            socket.timeout, Exception):
        return None


def slug_for_path(path):
    s = path.strip('/').replace('/', '_') or 'home'
    return s


def already_done(brand_dir):
    if not os.path.isdir(brand_dir):
        return False
    hits = 0
    for f in os.listdir(brand_dir):
        fp = os.path.join(brand_dir, f)
        if os.path.isfile(fp) and os.path.getsize(fp) > 2000:
            hits += 1
    return hits >= 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='re-scrape even if done')
    ap.add_argument('--only', help='comma-separated slugs to scrape (debug)')
    ap.add_argument('--sleep', type=float, default=1.5, help='seconds between requests')
    args = ap.parse_args()

    with open(SEED) as f:
        brands = json.load(f)
    if args.only:
        wanted = set(args.only.split(','))
        brands = [b for b in brands if b['slug'] in wanted]

    for b in brands:
        slug = b['slug']
        base = b['website'].rstrip('/')
        out_dir = os.path.join(RAW, slug)
        if not args.force and already_done(out_dir):
            print(f"[skip] {slug} (already has >=3 pages)")
            continue
        os.makedirs(out_dir, exist_ok=True)
        ok = 0
        for path in CANDIDATE_PATHS:
            url = urllib.parse.urljoin(base + '/', path.lstrip('/'))
            html = fetch(url)
            if html and len(html) > 1500:
                fname = slug_for_path(path) + '.html'
                with open(os.path.join(out_dir, fname), 'w') as f:
                    f.write(html)
                ok += 1
            time.sleep(args.sleep + random.random() * 0.5)
        print(f"[{ok:2d} pages] {slug} ({base})")

    print("\ndone")


if __name__ == '__main__':
    main()
