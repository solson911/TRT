#!/usr/bin/env python3
"""
Fetch the homepage (and a couple of likely services/about pages) for each
physical clinic in data/clinics.min.json. Extract clean text and save to
data/clinic_pages/{placeId}.json for later summarization by claude.

Resumable: skips any clinic that already has a non-empty file unless --force.
Polite: 1s sleep between domains, 0.3s between pages on same domain.

Usage:
  python3 scripts/fetch_clinic_pages.py --state arizona --city scottsdale
  python3 scripts/fetch_clinic_pages.py --limit 10
  python3 scripts/fetch_clinic_pages.py --only <placeId>,<placeId>
"""
import argparse, json, os, sys, time, re, socket
import urllib.parse, urllib.request, urllib.error
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'clinics.min.json')
OUT_DIR = os.path.join(ROOT, 'data', 'clinic_pages')
os.makedirs(OUT_DIR, exist_ok=True)

UA = 'Mozilla/5.0 (compatible; TRTIndexBot/0.1; +https://trtindex.com)'
DIRECTORY_CLASSES = {'primary_trt', 'offers_trt'}
socket.setdefaulttimeout(12)

# Pages on a domain that are most likely to contain service/pricing info.
# We try the homepage + up to 2 additional pages; if any return 404 quickly,
# skip. Kept short to respect clinic servers.
CANDIDATE_PATHS = ['/', '/services/', '/trt/', '/about/', '/testosterone/', '/hormone-therapy/']


def fetch_one(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,*/*;q=0.5'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype = r.headers.get('Content-Type', '')
            if 'html' not in ctype.lower():
                return None
            data = r.read(600_000)  # cap 600KB per page
            try:
                return data.decode(r.headers.get_content_charset() or 'utf-8', errors='replace')
            except Exception:
                return data.decode('utf-8', errors='replace')
    except Exception:
        return None


def clean_text(html):
    if not html: return ''
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'form', 'noscript', 'svg', 'iframe']):
        tag.decompose()
    # Prefer <main> if present
    main = soup.find('main') or soup.find('article') or soup.body or soup
    text = main.get_text(separator='\n', strip=True)
    # Collapse blank lines
    text = re.sub(r'\n{2,}', '\n\n', text)
    return text.strip()


def try_site(base_url):
    """Return a dict with homepage text plus one or two extra pages if found."""
    parsed = urllib.parse.urlparse(base_url if '://' in base_url else 'http://' + base_url)
    if not parsed.netloc:
        return None
    origin = f'{parsed.scheme or "https"}://{parsed.netloc}'

    out = {'origin': origin, 'pages': []}
    seen = set()
    for i, path in enumerate(CANDIDATE_PATHS):
        if len(out['pages']) >= 3: break
        url = origin + path
        if url in seen: continue
        seen.add(url)
        html = fetch_one(url)
        if not html: continue
        text = clean_text(html)
        if len(text) < 300: continue  # skip empty/placeholder pages
        out['pages'].append({'url': url, 'text': text[:8000]})
        if i > 0:
            time.sleep(0.3)
    return out if out['pages'] else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--state')
    ap.add_argument('--city')
    ap.add_argument('--only', help='comma-separated placeIds')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--sleep', type=float, default=1.0)
    args = ap.parse_args()

    with open(DATA) as f:
        raw = json.load(f)
    clinics = raw if isinstance(raw, list) else raw.get('clinics', [])
    clinics = [c for c in clinics if c.get('classification') in DIRECTORY_CLASSES and not c.get('telehealth')]
    if args.state: clinics = [c for c in clinics if c.get('stateSlug') == args.state]
    if args.city: clinics = [c for c in clinics if c.get('citySlug') == args.city]
    if args.only:
        wanted = set(args.only.split(','))
        clinics = [c for c in clinics if c.get('placeId') in wanted]

    ok = skipped = failed = 0
    for i, c in enumerate(clinics):
        if args.limit and (ok + failed) >= args.limit:
            break
        pid = c.get('placeId')
        if not pid: continue
        out_path = os.path.join(OUT_DIR, f'{pid}.json')
        if os.path.exists(out_path) and os.path.getsize(out_path) > 10 and not args.force:
            skipped += 1
            continue
        site = c.get('website')
        if not site:
            with open(out_path, 'w') as f: json.dump({'error': 'no-website'}, f)
            failed += 1
            continue

        print(f'[{i+1}/{len(clinics)}] {c["name"]} ({c["stateSlug"]}/{c["citySlug"]}) - {site}', file=sys.stderr)
        try:
            result = try_site(site)
        except Exception as e:
            result = None
        if not result:
            with open(out_path, 'w') as f: json.dump({'error': 'fetch-failed', 'website': site}, f)
            failed += 1
        else:
            result['placeId'] = pid
            result['clinicName'] = c.get('name')
            with open(out_path, 'w') as f: json.dump(result, f, ensure_ascii=False)
            ok += 1
        if ok % 25 == 0 and ok > 0:
            print(f'  ... checkpoint ok={ok} failed={failed} skipped={skipped}', file=sys.stderr)
        time.sleep(args.sleep)

    print(f'\ndone; ok={ok} skipped={skipped} failed={failed}')


if __name__ == '__main__':
    main()
