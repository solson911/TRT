#!/usr/bin/env python3
"""
biote_cleanup.py — post-mortem on the 2026-04-21 Biote ingestion.

After the overnight run we realized most Biote net-new records don't belong in
a *clinic* directory:
  - 1,403 had generic fallback names ("Biote Certified Provider — <city>, <ST>")
    → unclickable dead weight; remove outright.
  - 2,534 had practitioner names ("Jane Smith, MD") → people, not clinics.
    Keep the data but mark as classification=unrelated so they drop out of the
    public directory. (Could be surfaced in a future "Biote providers" section.)
  - 519 Biote-tagged *existing* records (source != chain:biote) stay put —
    those are real clinics where we just added a certification badge.

Run:
  python3 scripts/biote_cleanup.py           # apply cleanup
  python3 scripts/biote_cleanup.py --dry-run
"""
import argparse
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'public', 'data', 'clinics.min.json')

GENERIC_PREFIX = 'Biote Certified Provider'
NOW = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    with open(DATA_FILE) as f:
        clinics = json.load(f)

    before_total = len(clinics)
    dir_before = sum(1 for c in clinics if c.get('classification') in ('primary_trt', 'offers_trt'))

    kept = []
    removed_generic = 0
    reclassified_practitioner = 0

    for c in clinics:
        if c.get('source') == 'chain:biote':
            name = c.get('name', '')
            if name.startswith(GENERIC_PREFIX):
                removed_generic += 1
                continue  # drop
            # practitioner-named → reclassify rather than delete
            c['classification'] = 'unrelated'
            c['classificationReason'] = (
                'Biote-certified individual practitioner, not a clinic. '
                'Kept in dataset for possible future providers sub-directory.'
            )
            c['classificationConfidence'] = 'high'
            c['classificationModel'] = 'manual:biote-cleanup-2026-04-21'
            c['classificationAt'] = NOW
            reclassified_practitioner += 1
        kept.append(c)

    dir_after = sum(1 for c in kept if c.get('classification') in ('primary_trt', 'offers_trt'))

    print(f'[before] total: {before_total}, directory-visible: {dir_before}')
    print(f'[action] removed generic Biote: {removed_generic}')
    print(f'[action] reclassified practitioner Biote as unrelated: {reclassified_practitioner}')
    print(f'[after]  total: {len(kept)}, directory-visible: {dir_after}')
    print(f'         net directory delta: {dir_after - dir_before}')

    if args.dry_run:
        print('[dry-run] not saving')
        return

    kept.sort(key=lambda c: (c.get('stateSlug') or '', c.get('citySlug') or '', c.get('name') or ''))
    with open(DATA_FILE, 'w') as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)
    print(f'[save] wrote {len(kept)} records → {DATA_FILE}')


if __name__ == '__main__':
    main()
