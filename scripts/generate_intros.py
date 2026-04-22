#!/usr/bin/env python3
"""
generate_intros.py — per-state and per-city SEO intro paragraphs.

Shells out to the `claude` CLI so we don't double-bill the API against the
Max subscription. Writes to data/page-intros.json with the shape:
{
  "states": { "texas": { "intro": "...", "generatedAt": "..." } },
  "cities": { "texas/austin": { "intro": "...", "generatedAt": "..." } }
}

Usage:
  python3 scripts/generate_intros.py --pilot                  # 5 states + 10 cities; saves to disk
  python3 scripts/generate_intros.py --states                 # all 50 states (no cities)
  python3 scripts/generate_intros.py --cities --min-clinics 5 # cities w/ 5+ clinics first
  python3 scripts/generate_intros.py --cities                 # all 1,367 cities
  python3 scripts/generate_intros.py --force                  # re-generate existing entries
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict

sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLINICS_FILE = os.path.join(ROOT, 'data', 'clinics.min.json')
INTROS_FILE = os.path.join(ROOT, 'data', 'page-intros.json')

CLAUDE_BIN = os.environ.get('CLAUDE_BIN', 'claude')
CLAUDE_MODEL = os.environ.get('CLAUDE_MODEL', 'claude-haiku-4-5-20251001')
SAVE_EVERY = 25

DIRECTORY_CLASSES = {'primary_trt', 'offers_trt'}

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


def slug_to_name(slug):
    return ' '.join(w.capitalize() for w in slug.split('-'))


def claude_oneshot(prompt, timeout=120):
    """Run `claude -p PROMPT` and return stdout as a string.
    Raises on non-zero exit or timeout."""
    res = subprocess.run(
        [CLAUDE_BIN, '-p', '--model', CLAUDE_MODEL, prompt],
        capture_output=True, text=True, timeout=timeout,
    )
    if res.returncode != 0:
        raise RuntimeError(f'claude exited {res.returncode}: {res.stderr.strip()[:500]}')
    return res.stdout.strip()


def load_intros():
    if not os.path.exists(INTROS_FILE):
        return {'states': {}, 'cities': {}}
    with open(INTROS_FILE) as f:
        data = json.load(f)
    data.setdefault('states', {})
    data.setdefault('cities', {})
    return data


def save_intros(data):
    with open(INTROS_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def state_prompt(state_name, clinic_count, top_cities, top_chains):
    cities_line = ', '.join(top_cities[:6]) if top_cities else 'cities statewide'
    chains_line = ''
    if top_chains:
        chains_line = f' Notable multi-location providers operating here include {", ".join(top_chains[:3])}.'
    return f"""Write a single concise paragraph (2-3 sentences, 45-75 words) introducing TRT clinics in {state_name} for a directory landing page. Mention that there are {clinic_count} clinics across the state, concentrated in {cities_line}.{chains_line} Write in a neutral, informative tone — no hype, no marketing language, no testimonials. Do not start with "Welcome to" or "Looking for". Do not use the word "comprehensive". Do not output anything except the paragraph itself — no preamble, no quotes, no markdown."""


def city_prompt(city_name, state_name, clinic_count, avg_rating, top_clinics, services, chains_present):
    services_line = ''
    if services:
        services_line = f' Common services include {", ".join(services[:4])}.'
    chains_line = ''
    if chains_present:
        chains_line = ' Several national TRT chains operate locations here alongside independent practices.'
    rating_line = ''
    if avg_rating and avg_rating > 0:
        rating_line = f' The average Google rating across these clinics is {avg_rating:.1f} stars.'
    return f"""Write a single concise paragraph (2-3 sentences, 40-65 words) introducing TRT and hormone therapy clinics in {city_name}, {state_name} for a directory listing page. Reference that there are {clinic_count} clinics cataloged in the city.{services_line}{chains_line}{rating_line} Write in a neutral, informative tone — no hype, no marketing. Do not start with "Welcome to" or "Looking for". Do not use the word "comprehensive" or "premier". Do not output anything except the paragraph itself — no preamble, no quotes, no markdown."""


def summarize_state(all_clinics, state_slug):
    live = [c for c in all_clinics
            if c.get('stateSlug') == state_slug
            and c.get('classification') in DIRECTORY_CLASSES
            and c.get('businessStatus') != 'CLOSED_PERMANENTLY'
            and not c.get('telehealth')]
    city_counts = defaultdict(int)
    chain_counts = defaultdict(int)
    for c in live:
        if c.get('city'):
            city_counts[c['city']] += 1
        if c.get('chain'):
            chain = str(c['chain']).split(',')[0].strip()
            if chain.lower() != 'biote certified':
                chain_counts[chain] += 1
    top_cities = [c for c, _ in sorted(city_counts.items(), key=lambda x: -x[1])]
    top_chains = [c for c, n in sorted(chain_counts.items(), key=lambda x: -x[1]) if n >= 2]
    return {'count': len(live), 'top_cities': top_cities, 'top_chains': top_chains}


def summarize_city(all_clinics, state_slug, city_slug):
    live = [c for c in all_clinics
            if c.get('stateSlug') == state_slug and c.get('citySlug') == city_slug
            and c.get('classification') in DIRECTORY_CLASSES
            and c.get('businessStatus') != 'CLOSED_PERMANENTLY'
            and not c.get('telehealth')]
    rated = [c for c in live if c.get('rating')]
    avg = sum(c['rating'] for c in rated) / len(rated) if rated else 0
    city_name = next((c['city'] for c in live if c.get('city')), slug_to_name(city_slug))
    # top 3 clinics by rating*log10(1+reviews)
    def score(c):
        import math
        return (c.get('rating') or 0) * math.log10(1 + (c.get('ratingCount') or 0))
    top = sorted(live, key=lambda c: -score(c))[:3]
    services = set()
    for c in live:
        for s in c.get('services') or []:
            services.add(s)
    chains_present = any(c.get('chain') for c in live)
    return {
        'city_name': city_name,
        'count': len(live),
        'avg_rating': avg,
        'top_clinics': [c.get('name') for c in top],
        'services': sorted(services),
        'chains_present': chains_present,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pilot', action='store_true', help='5 states + 10 cities pilot')
    ap.add_argument('--states', action='store_true', help='Generate for all 50 states')
    ap.add_argument('--cities', action='store_true', help='Generate for cities (subject to --min-clinics)')
    ap.add_argument('--min-clinics', type=int, default=1, help='Only cities with at least N clinics')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--force', action='store_true', help='Re-generate even if entry exists')
    args = ap.parse_args()

    with open(CLINICS_FILE) as f:
        clinics = json.load(f)
    intros = load_intros()

    # Plan state + city work lists up front
    live = [c for c in clinics
            if c.get('classification') in DIRECTORY_CLASSES
            and c.get('businessStatus') != 'CLOSED_PERMANENTLY'
            and not c.get('telehealth')]
    state_slugs = sorted({c['stateSlug'] for c in live if c.get('stateSlug')})
    city_pairs = defaultdict(int)
    for c in live:
        if c.get('stateSlug') and c.get('citySlug'):
            city_pairs[(c['stateSlug'], c['citySlug'])] += 1
    # rank cities by clinic count desc so high-value pages get covered first
    city_list = [pair for pair, _ in sorted(city_pairs.items(), key=lambda x: -x[1])]
    city_list = [p for p in city_list if city_pairs[p] >= args.min_clinics]

    todo_states = []
    todo_cities = []
    if args.pilot:
        todo_states = ['texas', 'california', 'florida', 'new-york', 'arizona'][:5]
        # 10 highest-count cities
        todo_cities = city_list[:10]
    else:
        if args.states:
            todo_states = state_slugs
        if args.cities:
            todo_cities = city_list

    if args.limit:
        todo_states = todo_states[:args.limit]
        todo_cities = todo_cities[:args.limit]

    # Filter already-done unless --force
    if not args.force:
        todo_states = [s for s in todo_states if s not in intros['states']]
        todo_cities = [(st, ct) for (st, ct) in todo_cities if f'{st}/{ct}' not in intros['cities']]

    print(f'[plan] states: {len(todo_states)}, cities: {len(todo_cities)}')
    if not todo_states and not todo_cities:
        print('[done] nothing to do')
        return

    started = time.time()
    done = 0
    fails = 0

    for st in todo_states:
        state_name = US_STATES.get(st.upper().replace('-', ' ').replace(' ', '-'), slug_to_name(st))
        # lookup by slug against dict of slugified full names
        state_name_lookup = {slug_to_name(v).lower().replace(' ', '-'): v for v in US_STATES.values()}
        state_name = state_name_lookup.get(st, slug_to_name(st))
        summary = summarize_state(clinics, st)
        if summary['count'] == 0:
            print(f'[skip] {st}: 0 live clinics')
            continue
        prompt = state_prompt(state_name, summary['count'], summary['top_cities'], summary['top_chains'])
        try:
            intro = claude_oneshot(prompt)
        except Exception as e:
            print(f'[err state] {st}: {e}')
            fails += 1
            continue
        intros['states'][st] = {
            'intro': intro,
            'clinicCount': summary['count'],
            'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        done += 1
        print(f'[ok state] {st} ({summary["count"]}c) → {len(intro)}ch')
        if done % SAVE_EVERY == 0:
            save_intros(intros)
            print(f'[checkpoint] {done} generated, saved')

    for (st, ct) in todo_cities:
        state_name_lookup = {slug_to_name(v).lower().replace(' ', '-'): v for v in US_STATES.values()}
        state_name = state_name_lookup.get(st, slug_to_name(st))
        summary = summarize_city(clinics, st, ct)
        if summary['count'] == 0:
            print(f'[skip] {st}/{ct}: 0 live clinics')
            continue
        prompt = city_prompt(
            summary['city_name'], state_name, summary['count'], summary['avg_rating'],
            summary['top_clinics'], summary['services'], summary['chains_present'],
        )
        try:
            intro = claude_oneshot(prompt)
        except Exception as e:
            print(f'[err city] {st}/{ct}: {e}')
            fails += 1
            continue
        intros['cities'][f'{st}/{ct}'] = {
            'intro': intro,
            'clinicCount': summary['count'],
            'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        done += 1
        if done % 10 == 0:
            elapsed = time.time() - started
            rate = done / elapsed if elapsed > 0 else 0
            remaining = (len(todo_states) + len(todo_cities)) - done
            eta = remaining / rate if rate > 0 else 0
            print(f'[ok city] {st}/{ct} ({summary["count"]}c) → {len(intro)}ch  [{done}/{len(todo_states)+len(todo_cities)}, ~{eta/60:.1f} min remaining]')
        else:
            print(f'[ok city] {st}/{ct} ({summary["count"]}c) → {len(intro)}ch')
        if done % SAVE_EVERY == 0:
            save_intros(intros)
            print(f'[checkpoint] {done} generated, saved')

    save_intros(intros)
    elapsed = time.time() - started
    print(f'[done] generated {done} intros in {elapsed/60:.1f}m, {fails} failures')


if __name__ == '__main__':
    main()
