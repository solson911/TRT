#!/usr/bin/env python3
"""
For each clinic that has fetched page text in data/clinic_pages/{placeId}.json,
shell out to `claude -p` to produce a short structured writeup and save it to
data/clinic_writeups/{placeId}.json. Resumable; skips files that already exist.

Output schema:
  {
    "overview": "One-sentence plain-English summary of what this clinic does.",
    "highlights": ["Up to 3 short bullets of specific things the site mentions."],
    "services": ["TRT", "HRT", "Peptides"],   // subset of known tags the site mentions
    "consultModel": "in-person" | "hybrid" | "",
    "targetAudience": "One short sentence about the kind of patient this fits."
  }

Claude is prompted to return ONLY the JSON object. If parsing fails or text is
too thin we save a sentinel so we don't keep retrying.

Usage:
  python3 scripts/summarize_clinics.py --only <placeId>,<placeId>
  python3 scripts/summarize_clinics.py --limit 10
"""
import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_DIR = os.path.join(ROOT, 'data', 'clinic_pages')
OUT_DIR = os.path.join(ROOT, 'data', 'clinic_writeups')
os.makedirs(OUT_DIR, exist_ok=True)

PROMPT = """You are writing a brief, factual directory blurb for a TRT clinic. Use ONLY information that appears in the website text below. Do not invent facts. Do not include specific prices - leave pricing out entirely even if mentioned. Be neutral; do not use marketing adjectives like "premier", "elite", "world-class".

Return ONLY a single JSON object with this exact shape (no prose, no markdown fences):

{
  "overview": "1-2 sentences describing what the clinic does and how it delivers care.",
  "highlights": ["Up to 3 short factual bullets, each a noun phrase of something the site specifically mentions (e.g. 'In-house phlebotomy', 'Onsite labs', 'Also offers GLP-1 weight-loss program'). Use fewer if the site doesn't support three."],
  "services": ["Subset of: TRT, HRT, BHRT, Peptides, GLP-1, Wellness, Pellets that the site explicitly offers"],
  "consultModel": "in-person | hybrid | ''",
  "targetAudience": "One short sentence about who this clinic fits."
}

If the website text is too thin to support any of the fields, set that field to an empty string or empty array. Do NOT fabricate.

CLINIC NAME: __NAME__
WEBSITE TEXT:
---
__TEXT__
---
"""


def render_prompt(name, text):
    return PROMPT.replace('__NAME__', name or '').replace('__TEXT__', text or '')


def build_text(pages_record):
    pages = pages_record.get('pages') or []
    buf = []
    for p in pages:
        buf.append(f'[{p["url"]}]\n{p["text"]}')
    return '\n\n'.join(buf)[:14000]  # cap for claude CLI


def run_claude(prompt):
    # --print (-p) non-interactive; we pass the prompt via stdin for safety.
    # Timeout bumped to 300s (some clinic page bundles are large enough that
    # haiku takes >2min). TimeoutExpired is caught here so a single slow call
    # writes a sentinel and the batch continues; previously an unhandled
    # timeout killed the whole multi-hour run.
    try:
        result = subprocess.run(
            ['claude', '-p', '--model', 'haiku'],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    except Exception as e:
        return None, f'subprocess-error: {e}'
    if result.returncode != 0:
        return None, result.stderr.strip()
    return result.stdout.strip(), None


def parse_json(s):
    if not s: return None
    # Trim code fences if claude adds any
    s = s.strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[1] if '\n' in s else s
        if s.endswith('```'):
            s = s[:s.rfind('```')]
    # Find first { and last } to be robust against stray preamble
    a = s.find('{'); b = s.rfind('}')
    if a == -1 or b == -1 or b < a: return None
    try:
        return json.loads(s[a:b+1])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--only', help='comma-separated placeIds')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    only = set(args.only.split(',')) if args.only else None
    files = sorted(os.listdir(IN_DIR))
    ok = skipped = failed = 0
    for name in files:
        if not name.endswith('.json'): continue
        pid = name[:-5]
        if only and pid not in only: continue
        out_path = os.path.join(OUT_DIR, f'{pid}.json')
        if os.path.exists(out_path) and os.path.getsize(out_path) > 10 and not args.force:
            skipped += 1
            continue
        if args.limit and (ok + failed) >= args.limit:
            break

        with open(os.path.join(IN_DIR, name)) as f:
            rec = json.load(f)
        if rec.get('error') or not rec.get('pages'):
            with open(out_path, 'w') as f: json.dump({'skipped': 'no-page-text'}, f)
            failed += 1
            continue

        text = build_text(rec)
        if len(text) < 400:
            with open(out_path, 'w') as f: json.dump({'skipped': 'thin-text'}, f)
            failed += 1
            continue

        prompt = render_prompt(rec.get('clinicName', ''), text)
        print(f'[{ok+failed+1}] {pid} {rec.get("clinicName", "")}', file=sys.stderr)
        out, err = run_claude(prompt)
        parsed = parse_json(out)
        if not parsed:
            with open(out_path, 'w') as f: json.dump({'skipped': 'parse-failed', 'raw': (out or '')[:400]}, f)
            failed += 1
        else:
            with open(out_path, 'w') as f: json.dump(parsed, f, ensure_ascii=False)
            ok += 1
        if (ok + failed) % 20 == 0:
            print(f'  ...checkpoint ok={ok} failed={failed} skipped={skipped}', file=sys.stderr)

    print(f'\ndone; ok={ok} skipped={skipped} failed={failed}')


if __name__ == '__main__':
    main()
