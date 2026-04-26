#!/usr/bin/env python3
"""
exclude_non_trt_empties.py - Mark clinics as 'unrelated' for the directory
when their writeup came back empty AND manual review confirms they are not
TRT clinics (gambling-hijacked domains, hospital endocrinology departments,
marketing aggregators, professional associations, dead links, supplement
brands).

Conservative — only IDs hand-curated after sample inspection on 2026-04-26
make this list. Binary-garbage page-fetch failures are NOT excluded; those
need a page re-fetch fix.

Writes back to data/clinics.min.json in place. Idempotent.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'data', 'clinics.min.json')

EXCLUDE = {
    # gambling-hijacked domains (former clinics whose URLs now host casinos)
    'ChIJTZLZGoNtAHwRdi9NCI7wmus': 'Domain hosts gambling content (drcharlesarakaki.com)',
    'ChIJQwnIKF-l0ocR31wzvX9lcQQ': 'Domain hosts gambling content (littlerockmensclinic.com)',
    'ChIJg2idFOVFwokRG05R-iik-bo': 'Domain hosts gambling content (paukmanbioageclinic.com)',
    'ChIJQ-9SYW8TsocR6Diw-FG5plc': 'Domain hosts gambling content (vitalinjections.com)',

    # Hospital endocrinology departments — not TRT clinics
    'ChIJlYAJThEL3okRybCzEVnL6Vs': 'Hospital endocrinology dept (Albany Med)',
    'ChIJa0C3yUnM-YgRplh_bPRycHE': 'Hospital endocrinology dept (St Mary Athens)',
    'ChIJH-PQwRFlZIgRiIWBiycV9KU': 'Hospital concierge primary care (Vanderbilt Executive Wellness)',
    'ChIJMX0bQhXNFogREGn_nl3YKRg': 'Hospital endocrinology dept (South Bend Clinic)',
    'ChIJ2WBO4JZkz4cRdOeswN6_Ado': 'Hospital endocrinology dept (CoxHealth)',
    'ChIJG78Qf4dNyocRq6SzagX4r9I': 'Hospital endocrinology dept (Baptist Health)',
    'ChIJObu7xj09nIgRJpQaW9Z2Xo4': 'Hospital endocrinology dept (Memorial Physician Clinics)',
    'ChIJcdAb7AJP4okRJD5lsTSiS0U': 'Hospital endocrinology dept (Elliot Hospital)',
    'ChIJVa5gfeQHFogRiSyuYlCpSZk': 'Hospital integrative medicine (Parkview)',

    # Marketing / aggregator / professional association — not actual clinics
    'ChIJAQDQizFbMlER5NV1CcQ_ADc': 'Marketing aggregator (splinternetmarketing.com)',
    'ChIJVVUlepcd24gRHAF3a1HhP6E': 'Professional association (AMMG, not a clinic)',
    'ChIJPUfcjaTpwIkR-o7eh_DiMR0': 'Supplement brand store (Neo Nugenix)',

    # Dead site (bit.ly URL with no live destination content)
    'ChIJI9ntU6WrPogReke5h11EE2s': 'Dead/aggregator URL (bit.ly redirect)',

    # Parse-failed sentinel triage 2026-04-26: Claude correctly refused to
    # write a directory blurb because the website does not represent a TRT
    # clinic for the listed location.
    'ChIJNdSBZ6pkx4kRcDJqZli5uI0': 'B2B lead-gen service, not a clinic (Hormonetherapylead.com)',
    'ChIJMSJUvgivU4gRPVHbiMv-pGo': 'Chain mismatch: Winston-Salem listing but website covers only Florida (AgeRejuvenation)',
    'ChIJT62Ba3YOiYgRJ7yHHIQof-A': 'Spa/cosmetic beauty center, not TRT (Body Logic Wellness)',
    'ChIJuaeWrc0ZU4gRQdyLPOtDsO0': 'Urology practice (vasectomy/ED/infertility), no TRT focus (Dr. Luke Machen)',
}


def main():
    with open(DATA_FILE, 'r') as f:
        clinics = json.load(f)
    by_id = {c.get('placeId'): c for c in clinics}

    updated = 0
    already = 0
    missing = 0
    for pid, reason in EXCLUDE.items():
        c = by_id.get(pid)
        if c is None:
            print(f'  [skip-missing] {pid}', file=sys.stderr)
            missing += 1
            continue
        if c.get('classification') == 'unrelated':
            already += 1
            continue
        c['classification'] = 'unrelated'
        c['classificationConfidence'] = 'high'
        c['classificationReason'] = f'manual-exclude: {reason}'
        c['classificationModel'] = 'manual-2026-04-26'
        c['classificationAt'] = '2026-04-26'
        updated += 1

    if updated:
        with open(DATA_FILE, 'w') as f:
            json.dump(clinics, f, separators=(',', ':'))
    print(f'updated={updated} already-unrelated={already} missing={missing}')


if __name__ == '__main__':
    main()
