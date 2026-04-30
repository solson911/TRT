"""
clinics_io — read/write the sharded clinics dataset.

The dataset lives as per-state JSON files under data/clinics/{stateSlug}.json
(and data/clinics/_unknown.json for any record without a stateSlug). This
keeps any individual file under GitHub's 100MB hard limit even as the
directory grows.

For scripts: replace
    with open(DATA_FILE) as f: clinics = json.load(f)
    ...
    with open(DATA_FILE, 'w') as f: json.dump(clinics, f, ...)
with
    from lib.clinics_io import load_all, save_all
    clinics = load_all()
    ...
    save_all(clinics)

save_all() writes per-state shards atomically (temp file + rename per file)
so a partial failure can't leave a mixed-state dataset.
"""
import json
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHARDS_DIR = os.path.join(ROOT, 'data', 'clinics')
UNKNOWN_SHARD = '_unknown'


def _shard_key(clinic):
    return (clinic.get('stateSlug') or UNKNOWN_SHARD).strip().lower() or UNKNOWN_SHARD


def load_all():
    """Read all per-state shards and return a flat list of clinic records."""
    if not os.path.isdir(SHARDS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(SHARDS_DIR)):
        if not name.endswith('.json'):
            continue
        path = os.path.join(SHARDS_DIR, name)
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            out.extend(data)
    return out


def save_all(clinics):
    """Write the full clinic list back to per-state shards atomically.
    Each shard is sorted by (citySlug, name). Stale shards (state with no
    records in this list) are removed.
    """
    os.makedirs(SHARDS_DIR, exist_ok=True)
    by_shard = {}
    for c in clinics:
        by_shard.setdefault(_shard_key(c), []).append(c)
    # Atomic per-file write: temp + rename
    written = set()
    for shard, items in by_shard.items():
        items.sort(key=lambda c: (c.get('citySlug') or '', c.get('name') or ''))
        target = os.path.join(SHARDS_DIR, f'{shard}.json')
        fd, tmp = tempfile.mkstemp(prefix=f'.{shard}.', suffix='.json.tmp', dir=SHARDS_DIR)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(items, f, ensure_ascii=False, separators=(',', ':'))
            os.replace(tmp, target)
            written.add(f'{shard}.json')
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    # Drop stale shards
    for name in os.listdir(SHARDS_DIR):
        if name.endswith('.json') and name not in written:
            os.unlink(os.path.join(SHARDS_DIR, name))


def shard_path(state_slug):
    """Return the path of the shard a given stateSlug would live in."""
    key = (state_slug or UNKNOWN_SHARD).strip().lower() or UNKNOWN_SHARD
    return os.path.join(SHARDS_DIR, f'{key}.json')
