#!/usr/bin/env python3
"""
extract_telehealth.py - for each scraped brand, turn raw HTML into structured
JSON via `claude -p haiku`. Writes/updates data/telehealth.json.

Uses the user's Max subscription (no API bill), same pattern as
enrich_clinics.py. Resumable - skips brands already present in
telehealth.json unless --reclassify is given.

Output record shape (see src/data/telehealth.js for the full doc):
  slug, name, website, tagline, founded, medicalDirector, states,
  priceMin, priceMax, pricingTiers[{name, price, period, includes}],
  prescriberModel, consultModel, labsIncluded, labsShipped,
  medicationsShipped, treatmentOptions, insurance, fsaHsa, pros, cons,
  extractedAt, sourcePages
"""
import argparse, html, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, 'data', 'telehealth-seed-validated.json')
RAW = os.path.join(ROOT, 'data', 'telehealth-raw')
OUT = os.path.join(ROOT, 'data', 'telehealth.json')

MAX_CHARS_PER_PAGE = 8000
MAX_TOTAL_CHARS = 50000

SYSTEM_PROMPT = """You extract structured data about a TRT telehealth company from its own website copy.

Return ONLY a JSON object, no prose outside it. Use null when something isn't stated on the pages. Do NOT guess. Do NOT use em dashes or en dashes; plain hyphens only.

Schema:
{
  "tagline": "one short sentence the company uses, <=100 chars",
  "founded": <int year or null>,
  "medicalDirector": "full name if stated or null",
  "statesCovered": "all" | ["TX","CA",...] | null,
  "priceMin": <int USD per month or null>,
  "priceMax": <int USD per month or null>,
  "pricingTiers": [{"name":"...","price":<int>,"period":"monthly"|"one-time"|"quarterly","includes":"short summary"}],
  "prescriberModel": "physician"|"PA-NP"|"mixed"|null,
  "consultModel": "async"|"sync"|"both"|null,
  "labsIncluded": true|false|null,
  "labsShipped": true|false|null,
  "medicationsShipped": true|false|null,
  "treatmentOptions": ["injection","cream","pellets","oral","nasal",...],
  "insurance": true|false|null,
  "fsaHsa": true|false|null,
  "pros": ["short bullet", ...],
  "cons": ["short bullet", ...]
}

Rules:
- Prices are monthly USD unless a tier says otherwise. If a site says "$149 for first month then $99" use priceMin 99 priceMax 149.
- Only list pros/cons that are directly supported by the page copy. 2-4 each. Cons can include "pricing not disclosed on public pages" when applicable.
- statesCovered "all" only if the site explicitly says all 50 states (or all US states). Otherwise list states or null.
- If the site is mostly a peptide/medspa/weight-loss shop with TRT as a side service, note that in cons.
"""


def strip_html(text):
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.I)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.I)
    text = re.sub(r'<noscript[^>]*>.*?</noscript>', ' ', text, flags=re.DOTALL | re.I)
    text = re.sub(r'<!--.*?-->', ' ', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def page_priority(fname):
    # prioritize pricing/how-it-works/treatments over home+about when trimming
    name = fname.lower()
    if 'pricing' in name or 'price' in name or 'plan' in name or 'membership' in name or 'cost' in name:
        return 0
    if 'how' in name or 'process' in name or 'treatment' in name or 'service' in name or 'trt' in name or 'testoster' in name:
        return 1
    if 'team' in name or 'provider' in name or 'physician' in name or 'doctor' in name:
        return 2
    if 'home' in name:
        return 3
    if 'about' in name:
        return 4
    return 5


def load_brand_text(slug):
    d = os.path.join(RAW, slug)
    if not os.path.isdir(d):
        return "", []
    files = sorted(os.listdir(d), key=page_priority)
    parts, used = [], []
    total = 0
    for fn in files:
        fp = os.path.join(d, fn)
        if not fn.endswith('.html'):
            continue
        try:
            with open(fp) as f:
                raw = f.read()
        except Exception:
            continue
        stripped = strip_html(raw)
        if len(stripped) < 200:
            continue
        snippet = stripped[:MAX_CHARS_PER_PAGE]
        label = fn.replace('.html', '')
        block = f"=== {label} ===\n{snippet}\n"
        if total + len(block) > MAX_TOTAL_CHARS:
            break
        parts.append(block)
        used.append(label)
        total += len(block)
    return "\n".join(parts), used


def call_claude(system, user, model='haiku'):
    cmd = ['claude', '-p', '--model', model, '--append-system-prompt', system]
    try:
        p = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    if p.returncode != 0:
        return None, f'exit {p.returncode}: {p.stderr[:200]}'
    return p.stdout, None


def parse_json(text):
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', t, re.DOTALL)
    if m:
        t = m.group(1)
    m = re.search(r'\{.*\}', t, re.DOTALL)
    if m:
        t = m.group(0)
    try:
        return json.loads(t)
    except Exception as e:
        return None


def load_existing():
    if os.path.exists(OUT):
        with open(OUT) as f:
            return json.load(f)
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reclassify', action='store_true')
    ap.add_argument('--only', help='comma-separated slugs')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args()

    with open(SEED) as f:
        brands = json.load(f)
    if args.only:
        wanted = set(args.only.split(','))
        brands = [b for b in brands if b['slug'] in wanted]
    existing = {b['slug']: b for b in load_existing()}

    results = list(existing.values())
    done = 0
    for b in brands:
        if args.limit and done >= args.limit:
            break
        slug = b['slug']
        if not args.reclassify and slug in existing and existing[slug].get('extractedAt'):
            continue
        text, used = load_brand_text(slug)
        if not text:
            print(f"[--] {slug}: no scraped text, skipping")
            continue
        user = (f"Brand: {b['name']}\nWebsite: {b['website']}\n\n"
                f"Website copy (multiple pages):\n\n{text}")
        out, err = call_claude(SYSTEM_PROMPT, user)
        if err or not out:
            print(f"[XX] {slug}: claude error ({err})")
            continue
        data = parse_json(out)
        if not data:
            print(f"[??] {slug}: could not parse JSON")
            continue
        data.update({
            'slug': slug,
            'name': b['name'],
            'website': b['website'],
            'extractedAt': time.strftime('%Y-%m-%d'),
            'sourcePages': used,
        })
        existing[slug] = data
        results = list(existing.values())
        results.sort(key=lambda x: x['slug'])
        with open(OUT, 'w') as f:
            json.dump(results, f, indent=2)
        price = data.get('priceMin') or data.get('priceMax')
        print(f"[OK] {slug:30s} price=${price}  pages={len(used)}")
        done += 1

    print(f"\n{len(results)} brands in {OUT}")


if __name__ == '__main__':
    main()
