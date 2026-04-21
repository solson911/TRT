#!/usr/bin/env python3
"""
enrich_clinics.py — classify each clinic as TRT-relevant via the claude CLI.

Uses `claude -p --model haiku` so the work runs against the user's Max
subscription, not the Anthropic API (no double billing).

Writes the classification back onto the record in public/data/clinics.min.json:

  classification: 'primary_trt' | 'offers_trt' | 'unrelated'
  classificationConfidence: 'high' | 'medium' | 'low'
  classificationReason: short string
  classificationAt: ISO timestamp
  classificationModel: 'claude-haiku-4-5'

Resumable: records that already have classificationAt are skipped unless
--reclassify is passed. Checkpoints to disk every 200 calls.

Usage:
  python3 scripts/enrich_clinics.py                 # full run
  python3 scripts/enrich_clinics.py --limit 20      # smoke test
  python3 scripts/enrich_clinics.py --states TX,CA  # scope by state
  python3 scripts/enrich_clinics.py --reclassify    # redo everything
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'public', 'data', 'clinics.min.json')
MODEL = 'haiku'
SAVE_EVERY = 200
BATCH_SIZE = 20

US_STATE_ABBRS = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','DC','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM',
    'NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA',
    'WV','WI','WY',
}

SYSTEM_PROMPT = """You classify business listings to decide whether they are TRT (Testosterone Replacement Therapy) or men's hormone clinics — the kind of clinic a man visits to have testosterone checked and treated.

Categories:
- "primary_trt": clinic's primary focus is TRT or men's hormone optimization. Examples: Gameday Men's Health, Low T Center, Craft Men's Clinic, Renew Vitality, clinics with "testosterone" or "TRT" or "men's health" in the name.
- "offers_trt": broader medical or wellness practice that clearly offers TRT alongside other services. Examples: functional medicine clinics, concierge primary care, anti-aging medicine clinics with clear hormone focus, men's wellness clinics.
- "unrelated": not a TRT clinic. Examples: dentists, OB/GYN, pediatrics, physical therapy, dermatology, mental health counselors, generic weight-loss chains, chiropractors, veterinary, hospitals, non-medical businesses, hospices, senior living.

Rules:
- If unsure, prefer "unrelated" with low confidence over guessing "offers_trt".
- A weight-loss clinic is only "offers_trt" if name/website/types explicitly reference TRT or hormone therapy.
- A med-spa is "offers_trt" only if hormone therapy is obviously part of the business.
- Output ONLY a JSON object. No markdown fences, no prose outside the JSON."""

BATCH_USER_TEMPLATE = """Classify each business below. Output ONLY a JSON array with one object per business, in the SAME ORDER as the input. Each object: {{"id": <int>, "classification":"primary_trt"|"offers_trt"|"unrelated", "confidence":"high"|"medium"|"low", "reason":"10-word explanation"}}.

Businesses:
{items}"""


def strip_fences(text):
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', t, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r'(\{[^{}]*"classification"[^{}]*\})', t, re.DOTALL)
    if m:
        return m.group(1)
    return t


def strip_array_fences(text):
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', t, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r'(\[\s*\{.*\}\s*\])', t, re.DOTALL)
    if m:
        return m.group(1)
    return t


def classify_batch(clinics):
    """Classify a batch of clinic records. Returns list of result dicts in input order,
    each with {classification, confidence, reason}. Errors get classification='error'."""
    items_lines = []
    for i, c in enumerate(clinics):
        items_lines.append(
            f"[{i}] name={c.get('name') or '(unknown)'} | "
            f"address={c.get('address') or '(unknown)'} | "
            f"website={c.get('website') or '(none)'} | "
            f"types={', '.join(c.get('types') or []) or '(none)'}"
        )
    prompt = BATCH_USER_TEMPLATE.format(items='\n'.join(items_lines))
    cmd = [
        'claude', '-p',
        '--model', MODEL,
        '--output-format', 'json',
        '--system-prompt', SYSTEM_PROMPT,
        '--no-session-persistence',
        prompt,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return [{'classification': 'error', 'confidence': 'low', 'reason': 'cli timeout'} for _ in clinics]
    if proc.returncode != 0:
        err = f'cli rc={proc.returncode}: {(proc.stderr or proc.stdout)[:200]}'
        return [{'classification': 'error', 'confidence': 'low', 'reason': err} for _ in clinics]
    try:
        envelope = json.loads(proc.stdout)
    except Exception as e:
        err = f'envelope parse: {e}: {proc.stdout[:120]}'
        return [{'classification': 'error', 'confidence': 'low', 'reason': err} for _ in clinics]
    if envelope.get('is_error'):
        err = f'cli error: {envelope.get("result", "")[:200]}'
        return [{'classification': 'error', 'confidence': 'low', 'reason': err} for _ in clinics]
    raw_result = envelope.get('result', '')
    try:
        parsed = json.loads(strip_array_fences(raw_result))
        if not isinstance(parsed, list):
            raise ValueError('not a list')
        # Match by id if the model emitted ids, otherwise by position
        by_id = {}
        for item in parsed:
            if isinstance(item, dict) and 'id' in item:
                by_id[item['id']] = item
        out = []
        for i in range(len(clinics)):
            item = by_id.get(i) if by_id else (parsed[i] if i < len(parsed) else None)
            if not item:
                out.append({'classification': 'error', 'confidence': 'low', 'reason': 'missing in response'})
                continue
            cls = item.get('classification')
            if cls not in ('primary_trt', 'offers_trt', 'unrelated'):
                out.append({'classification': 'error', 'confidence': 'low', 'reason': f'bad cls: {cls!r}'})
                continue
            out.append({
                'classification': cls,
                'confidence': item.get('confidence', 'medium'),
                'reason': (item.get('reason') or '').strip()[:200],
            })
        return out
    except Exception as e:
        err = f'batch parse: {e}: {raw_result[:200]}'
        return [{'classification': 'error', 'confidence': 'low', 'reason': err} for _ in clinics]


def save(records):
    with open(DATA_FILE, 'w') as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--states', type=str, default='')
    ap.add_argument('--reclassify', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    with open(DATA_FILE) as f:
        records = json.load(f)
    print(f'[start] loaded {len(records)} records')

    states_scope = {s.strip().upper() for s in args.states.split(',') if s.strip()}

    targets = []
    for i, r in enumerate(records):
        if states_scope and r.get('state') not in states_scope:
            continue
        if r.get('state') and r.get('state') not in US_STATE_ABBRS:
            continue
        if not args.reclassify and r.get('classificationAt'):
            continue
        targets.append(i)
        if args.limit and len(targets) >= args.limit:
            break

    print(f'[plan] will classify {len(targets)} records'
          + (f' (scoped to {sorted(states_scope)})' if states_scope else '')
          + (f' [limit={args.limit}]' if args.limit else ''))
    if args.dry_run:
        return

    processed = 0
    err_count = 0
    cls_counts = {'primary_trt': 0, 'offers_trt': 0, 'unrelated': 0, 'error': 0}
    t0 = time.time()
    last_checkpoint = 0

    for batch_start in range(0, len(targets), BATCH_SIZE):
        batch_indices = targets[batch_start:batch_start + BATCH_SIZE]
        batch_clinics = [records[i] for i in batch_indices]
        results = classify_batch(batch_clinics)

        now_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        for idx, result in zip(batch_indices, results):
            r = records[idx]
            cls_counts[result['classification']] = cls_counts.get(result['classification'], 0) + 1
            if result['classification'] == 'error':
                err_count += 1
                print(f'[err] #{idx} {r.get("name")!r}: {result["reason"]}')
            else:
                r['classification'] = result['classification']
                r['classificationConfidence'] = result['confidence']
                r['classificationReason'] = result['reason']
                r['classificationModel'] = 'claude-haiku-4-5'
                r['classificationAt'] = now_iso
        processed += len(batch_indices)

        elapsed = time.time() - t0
        rate = processed / elapsed if elapsed else 0
        remaining = (len(targets) - processed) / rate if rate else 0
        print(f'[progress] {processed}/{len(targets)}  '
              f'primary={cls_counts["primary_trt"]} offers={cls_counts["offers_trt"]} '
              f'unrelated={cls_counts["unrelated"]} errors={err_count}  '
              f'{rate:.2f}/s  ETA {remaining/60:.1f} min')

        if processed - last_checkpoint >= SAVE_EVERY:
            save(records)
            last_checkpoint = processed
            print(f'[checkpoint] saved at {processed} classifications')

    save(records)
    print(f'[done] {processed} classifications in {(time.time()-t0)/60:.1f} min. '
          f'primary={cls_counts["primary_trt"]} offers={cls_counts["offers_trt"]} '
          f'unrelated={cls_counts["unrelated"]} errors={err_count}')


if __name__ == '__main__':
    main()
