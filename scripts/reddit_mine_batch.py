#!/usr/bin/env python3
"""
Batch runner for reddit_mine.py. Iterates telehealth.json and invokes
reddit_mine.py per brand. Resumable: skips brands that already have a
summary file unless --force is passed.
"""
import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'telehealth.json')
OUT_DIR = os.path.join(ROOT, 'data', 'telehealth-reddit')
MINE = os.path.join(ROOT, 'scripts', 'reddit_mine.py')
os.makedirs(OUT_DIR, exist_ok=True)

# Brands with names that are too generic to search well on Reddit without
# hallucinating noise. Use manually curated aliases here.
ALIAS_OVERRIDES = {
    '1st-optimal': '1st Optimal,1stOptimal',
    'alpha-md': 'AlphaMD,alpha-md,alphamd.org',
    'advanced-trt-clinic': 'advancedtrtclinic',
    'biote': 'biote,bhrt pellets,biote pellets',
    'royal-medical-center': 'royalmedical,royal medical trt',
    'mens-t-clinic': "mens t clinic,menstclinic",
    'ageless-mens-health': 'ageless mens health,agelessmen',
    'age-rejuvenation': 'agerejuvenation',
    'nrg-clinic': 'nrgclinic,nrg trt',
    'mens-vitality-center': 'mens vitality center',
    'modern-wellness-clinic': 'modern wellness clinic,modernwellnessclinic',
    'regenx-health': 'regenx health,regenxhealth',
    'renew-youth': 'renew youth,renewyouth',
    'hone-health': 'hone health,honehealth',
    'blokes': 'blokes trt,joiandblokes,blokes.co',
    'maximus-tribe': 'maximus tribe,maximustribe',
}


def aliases_for(slug, name):
    if slug in ALIAS_OVERRIDES:
        return ALIAS_OVERRIDES[slug]
    # default: name + name-without-spaces
    compact = name.replace(' ', '').replace("'", '')
    s = {name, compact}
    return ','.join(sorted(s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--only', help='comma-separated slugs')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args()

    with open(DATA) as f:
        brands = json.load(f)
    if args.only:
        wanted = set(args.only.split(','))
        brands = [b for b in brands if b['slug'] in wanted]

    done = 0
    for b in brands:
        if args.limit and done >= args.limit:
            break
        slug = b['slug']
        md_path = os.path.join(OUT_DIR, f'{slug}.md')
        if os.path.exists(md_path) and not args.force:
            continue
        aliases = aliases_for(slug, b['name'])
        print(f'=== {slug} :: {aliases}')
        cmd = [sys.executable, MINE, '--slug', slug, '--name', b['name'], '--aliases', aliases]
        try:
            p = subprocess.run(cmd, timeout=900)
        except subprocess.TimeoutExpired:
            print(f'  [TIMEOUT] {slug}')
            continue
        if p.returncode != 0:
            print(f'  [FAIL] {slug} exit={p.returncode}')
            continue
        done += 1
        time.sleep(8)  # breathe between brands; Reddit rate-limit courtesy

    print(f'\ndone; {done} brands mined this run')


if __name__ == '__main__':
    main()
