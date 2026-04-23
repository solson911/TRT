#!/usr/bin/env python3
"""
clean_telehealth.py - post-processing after extract + review generation.

- Strips the claude CLI preamble from review .md files (anything before the
  first H2).
- Drops brands whose scraping went sideways or whose review flags them as
  not-a-TRT-clinic / wrong-data.
- Re-picks priceMin/priceMax from the TRT-specific pricing tier when the
  extractor grabbed the cheapest non-TRT tier (hair / ED / labs-only).
- Replaces em/en dashes site-wide in review bodies with plain hyphens.
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'telehealth.json')
REVIEWS = os.path.join(ROOT, 'data', 'telehealth-reviews')

# Brands to drop outright: wrong company scraped, or not actually TRT-focused.
# Each entry: slug -> reason (for audit).
DROP = {
    'vault-health': 'scraper hit Vault Workforce Screening (unrelated company)',
    'jase-medical': 'preparedness/chronic-supply company, not a TRT clinic',
    'peter-md': 'scraper hit personal blog, not the real PeterMD site',
    'ro': 'ED/weight-loss platform; no real TRT product',
    'hims': 'hair/ED platform; TRT is a fringe offering with no public pricing',
    # brands with no extracted price AND no useful pages - nothing to stand on
    'marek-health': 'only 1 scraped page, all data fields null',
    'innovative-men': 'local Seattle clinic with no telehealth price data',
}


def strip_preamble(text):
    """Return text starting at the first '## ' heading."""
    idx = text.find('\n## ')
    if idx < 0:
        idx = text.find('## ')
        if idx < 0:
            return text
    # Keep from the '## ' marker (or newline just before it).
    start = idx if text[idx] == '#' else idx + 1
    return text[start:].lstrip()


def strip_dashes(text):
    return text.replace('\u2014', '-').replace('\u2013', '-')


def trt_tier_price(b):
    """Find the TRT-specific tier and return (min, max) monthly USD, or None."""
    tiers = b.get('pricingTiers') or []
    trt_tiers = []
    for t in tiers:
        name = (t.get('name') or '').lower()
        if any(k in name for k in ('testoster', 'trt', 'injection', 'inject', 'cypionate', 'enanthate')):
            if t.get('price') and (t.get('period') in (None, 'monthly')):
                trt_tiers.append(t['price'])
    if not trt_tiers:
        return None
    return min(trt_tiers), max(trt_tiers)


def main():
    with open(DATA) as f:
        brands = json.load(f)

    kept, dropped = [], []
    for b in brands:
        slug = b['slug']
        if slug in DROP:
            dropped.append((slug, DROP[slug]))
            # Also remove review file
            md = os.path.join(REVIEWS, f'{slug}.md')
            if os.path.exists(md):
                os.remove(md)
            continue

        # Adjust priceMin/priceMax if a TRT-specific tier exists
        prices = trt_tier_price(b)
        if prices:
            lo, hi = prices
            if b.get('priceMin') != lo or b.get('priceMax') != hi:
                b['priceMin'], b['priceMax'] = lo, hi

        kept.append(b)

    kept.sort(key=lambda x: x['slug'])
    with open(DATA, 'w') as f:
        json.dump(kept, f, indent=2)

    # Clean review markdown files
    fixed_md = 0
    for slug in [b['slug'] for b in kept]:
        md = os.path.join(REVIEWS, f'{slug}.md')
        if not os.path.exists(md):
            continue
        with open(md) as f:
            before = f.read()
        after = strip_dashes(strip_preamble(before))
        if after != before:
            with open(md, 'w') as f:
                f.write(after)
            fixed_md += 1

    print(f"kept {len(kept)} brands, dropped {len(dropped)}, fixed {fixed_md} review files")
    for slug, reason in dropped:
        print(f"  - dropped {slug}: {reason}")


if __name__ == '__main__':
    main()
