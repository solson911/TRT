#!/usr/bin/env python3
"""One-shot migration: split data/clinics.min.json into per-state shards
under data/clinics/{stateSlug}.json. Idempotent. Drop the monolith via
.gitignore after this runs.
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib.clinics_io import load_all, save_all, SHARDS_DIR

ROOT = os.path.dirname(HERE)
MONOLITH = os.path.join(ROOT, 'data', 'clinics.min.json')


def main():
    if not os.path.exists(MONOLITH):
        print(f'no monolith at {MONOLITH}; nothing to migrate')
        return
    with open(MONOLITH) as f:
        clinics = json.load(f)
    print(f'loaded {len(clinics)} records from monolith')
    save_all(clinics)
    print(f'wrote shards to {SHARDS_DIR}')
    # Verify round-trip
    roundtrip = load_all()
    if len(roundtrip) != len(clinics):
        print(f'ERROR: round-trip mismatch: monolith={len(clinics)} shards={len(roundtrip)}')
        sys.exit(2)
    src_ids = {c.get('placeId') for c in clinics}
    rt_ids = {c.get('placeId') for c in roundtrip}
    if src_ids != rt_ids:
        print(f'ERROR: placeId set differs after round-trip')
        sys.exit(2)
    print(f'verified: {len(roundtrip)} records round-trip cleanly')
    # Show shard sizes
    sizes = []
    for name in sorted(os.listdir(SHARDS_DIR)):
        if name.endswith('.json'):
            sizes.append((name, os.path.getsize(os.path.join(SHARDS_DIR, name))))
    print(f'\nshard sizes (top 10):')
    for name, sz in sorted(sizes, key=lambda x: -x[1])[:10]:
        print(f'  {name:30} {sz/1024/1024:6.2f} MB')
    total = sum(sz for _, sz in sizes)
    print(f'  total: {total/1024/1024:.2f} MB across {len(sizes)} shards')


if __name__ == '__main__':
    main()
