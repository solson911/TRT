#!/usr/bin/env python3
"""
write_telehealth_reviews.py - for each brand in telehealth.json, draft a
600-900 word editorial review via `claude -p sonnet`. Output goes to
data/telehealth-reviews/{slug}.md.

Why sonnet and not haiku: narrative quality matters for the site's
E-E-A-T signal. Haiku generates flat copy that reads like marketing
paraphrase. Sonnet stays grounded in the extracted facts and reads like a
human editor.

Resumable: skip brands that already have a review file unless --force.
"""
import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRACTED = os.path.join(ROOT, 'data', 'telehealth.json')
RAW = os.path.join(ROOT, 'data', 'telehealth-raw')
OUT_DIR = os.path.join(ROOT, 'data', 'telehealth-reviews')
os.makedirs(OUT_DIR, exist_ok=True)

SYSTEM_PROMPT = """You are an editor for TRT Index, a non-sponsored directory of TRT and men's hormone clinics. You write honest, grounded editorial reviews of telehealth TRT companies.

House style:
- 600-900 words.
- Plain hyphens only. Never em dashes, never en dashes.
- Conversational but informed. No marketing-speak, no corporate cliches ("game-changer", "cutting-edge", "revolutionary").
- No clickbait. No affiliate-style pitch.
- Cite specific facts from the provided data. Don't invent pricing or states.
- Structure: opening paragraph (who they are + who they're for), How it works, Pricing, What's included, Pros & cons (prose, not bullets), Bottom line.
- When pricing is unclear, say so plainly: "Public pricing isn't disclosed; you'll get a quote after the intake."
- Don't guarantee outcomes or make medical claims. Testosterone is a Schedule III controlled substance; readers need a real evaluation.
- Don't name prescribing physicians unless they are explicitly listed as the medical director on the company's own site.
- End with a short disclaimer sentence: "Pricing and state coverage verified {DATE}; check the provider's site for the latest."

Output ONLY the article body as Markdown, starting with an H2. No H1, no frontmatter, no preamble. The title goes in a later step."""


def call_claude(system, user, model='sonnet'):
    cmd = ['claude', '-p', '--model', model, '--append-system-prompt', system]
    try:
        p = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=360)
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    if p.returncode != 0:
        return None, f'exit {p.returncode}: {p.stderr[:200]}'
    return p.stdout.strip(), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--only', help='comma-separated slugs')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--model', default='sonnet')
    args = ap.parse_args()

    with open(EXTRACTED) as f:
        brands = json.load(f)
    if args.only:
        wanted = set(args.only.split(','))
        brands = [b for b in brands if b['slug'] in wanted]

    today = time.strftime('%B %Y')
    sys_prompt = SYSTEM_PROMPT.replace('{DATE}', today)

    done = 0
    for b in brands:
        if args.limit and done >= args.limit:
            break
        slug = b['slug']
        out_path = os.path.join(OUT_DIR, f'{slug}.md')
        if os.path.exists(out_path) and not args.force:
            continue

        # Strip verbose fields before handing to the model.
        facts = {k: v for k, v in b.items()
                 if k not in ('sourcePages', 'extractedAt')}
        user = (f"Write the review body for this telehealth TRT company:\n\n"
                f"{json.dumps(facts, indent=2)}\n\n"
                f"Start with '## How {b['name']} works' and follow the house structure.")

        out, err = call_claude(sys_prompt, user, model=args.model)
        if err or not out:
            print(f"[XX] {slug}: {err}")
            continue
        # Strip leading code fences if the model wrapped output
        if out.startswith('```'):
            out = out.strip('`').lstrip('markdown').strip()
        with open(out_path, 'w') as f:
            f.write(out + '\n')
        words = len(out.split())
        print(f"[OK] {slug:30s} {words} words")
        done += 1

    print(f"\ndone; reviews in {OUT_DIR}")


if __name__ == '__main__':
    main()
