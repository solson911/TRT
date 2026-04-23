#!/usr/bin/env python3
"""
reddit_mine.py - pilot script for harvesting Reddit mentions of a TRT
telehealth brand and producing an editorial summary via `claude -p sonnet`.

Usage:
  python3 reddit_mine.py --slug hone-health --name "Hone Health" --aliases "hone,honehealth,hone health"

Output:
  data/telehealth-reddit/{slug}.json  -- raw pulled posts + comments
  data/telehealth-reddit/{slug}.md    -- editorial summary (no verbatim quotes)

No auth needed for Reddit's public JSON endpoints. Polite UA + throttle.
"""
import argparse, json, os, re, subprocess, sys, time
import urllib.request, urllib.parse, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'data', 'telehealth-reddit')
os.makedirs(OUT_DIR, exist_ok=True)

UA = "TRTIndexBot/0.1 (+https://trtindex.com; editorial research)"

# Subreddits where TRT telehealth actually gets discussed.
SUBREDDITS = ['trt', 'Testosterone', 'TRT_Telemedicine', 'PeterAttia', 'Hormones']


def fetch_json(url, timeout=20, retries=3):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    return None
                return json.loads(r.read().decode('utf-8', errors='replace'))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 10 * (2 ** attempt)  # 10s, 20s, 40s
                print(f'  429 backoff {wait}s...', file=sys.stderr)
                time.sleep(wait)
                continue
            print(f'  fetch err {url[:80]}: {e}', file=sys.stderr)
            return None
        except (urllib.error.URLError, Exception) as e:
            if attempt < retries - 1:
                time.sleep(3)
                continue
            print(f'  fetch err {url[:80]}: {e}', file=sys.stderr)
            return None
    return None


def search_sub(sub, query, limit=50):
    q = urllib.parse.quote(f'"{query}"')
    url = (f'https://www.reddit.com/r/{sub}/search.json?q={q}&restrict_sr=on'
           f'&limit={limit}&sort=relevance&t=all')
    data = fetch_json(url)
    if not data:
        return []
    return [c['data'] for c in data.get('data', {}).get('children', [])]


def search_all(query, limit=50):
    q = urllib.parse.quote(f'"{query}"')
    url = f'https://www.reddit.com/search.json?q={q}&limit={limit}&sort=relevance&t=all'
    data = fetch_json(url)
    if not data:
        return []
    return [c['data'] for c in data.get('data', {}).get('children', [])]


def fetch_comments(permalink, limit=30):
    # permalink is like "/r/trt/comments/abc123/title/"
    url = f'https://www.reddit.com{permalink}.json?limit={limit}&depth=2'
    data = fetch_json(url)
    if not data or len(data) < 2:
        return []
    comments = []
    def walk(children):
        for c in children:
            cd = c.get('data', {})
            body = cd.get('body')
            if body and body not in ('[deleted]', '[removed]'):
                comments.append({
                    'body': body[:1500],
                    'score': cd.get('score', 0),
                    'author': cd.get('author', '?'),
                })
            replies = cd.get('replies')
            if isinstance(replies, dict):
                walk(replies.get('data', {}).get('children', []))
    walk(data[1].get('data', {}).get('children', []))
    # keep top-scored
    comments.sort(key=lambda c: -c['score'])
    return comments[:15]


def harvest(slug, name, aliases):
    print(f'[{slug}] harvesting for aliases: {aliases}')
    all_posts = {}
    for alias in aliases:
        for sub in SUBREDDITS:
            posts = search_sub(sub, alias, limit=25)
            for p in posts:
                pid = p.get('id')
                if not pid:
                    continue
                all_posts[pid] = p
            time.sleep(1.0)
        global_posts = search_all(alias, limit=25)
        for p in global_posts:
            pid = p.get('id')
            if pid and pid not in all_posts:
                all_posts[pid] = p
        time.sleep(1.0)

    # Rank by score * (1 + log(num_comments))
    import math
    ranked = sorted(all_posts.values(),
                    key=lambda p: p.get('score', 0) * (1 + math.log(1 + p.get('num_comments', 0))),
                    reverse=True)
    top = ranked[:20]

    # Pull top comments for top 10
    results = []
    for p in top[:10]:
        permalink = p.get('permalink', '')
        comments = fetch_comments(permalink) if permalink else []
        time.sleep(1.0)
        results.append({
            'id': p.get('id'),
            'subreddit': p.get('subreddit'),
            'title': p.get('title'),
            'selftext': (p.get('selftext') or '')[:2000],
            'score': p.get('score', 0),
            'num_comments': p.get('num_comments', 0),
            'url': f'https://www.reddit.com{permalink}',
            'comments': comments,
        })
    # metadata-only entries for the rest (ranked 11-20)
    for p in top[10:]:
        results.append({
            'id': p.get('id'),
            'subreddit': p.get('subreddit'),
            'title': p.get('title'),
            'selftext': (p.get('selftext') or '')[:500],
            'score': p.get('score', 0),
            'num_comments': p.get('num_comments', 0),
            'url': f'https://www.reddit.com{p.get("permalink", "")}',
            'comments': [],
        })

    print(f'[{slug}] {len(all_posts)} unique posts, summarizing top {len(results)}')
    return results


SYS_PROMPT = """You are an editor for TRT Index, writing a short editorial summary of what Reddit users say about a telehealth TRT brand.

House rules:
- 180-280 words.
- No verbatim quotes. Do NOT copy Reddit text. Paraphrase themes.
- No usernames. No post titles. Reference "Reddit users" / "posters in r/trt" generally.
- Plain hyphens only. Never em dashes or en dashes.
- Balance: lead with the most-cited positives, then the most-cited negatives, then a short synthesis.
- If the signal is thin (few posts, low engagement), say so plainly rather than over-indexing.
- Don't guess at facts not present. Don't add marketing gloss.
- End with: "Source: aggregated Reddit discussions; individual experiences vary."

Structure:
## What Reddit users say

(one paragraph positives + one paragraph negatives + short synthesis)"""


def call_claude(system, user, model='sonnet'):
    cmd = ['claude', '-p', '--model', model, '--append-system-prompt', system]
    try:
        p = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=360)
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    if p.returncode != 0:
        return None, f'exit {p.returncode}: {p.stderr[:200]}'
    return p.stdout.strip(), None


def summarize(slug, name, posts):
    if not posts:
        return None, 'no posts found'
    # Build a compact text bundle the model can read
    parts = [f"Brand: {name}\n\nReddit discussions (top-ranked by engagement):\n"]
    for i, p in enumerate(posts, 1):
        parts.append(f"\n--- Post {i} (r/{p['subreddit']}, score={p['score']}, comments={p['num_comments']}) ---")
        parts.append(f"Title: {p['title']}")
        if p['selftext']:
            parts.append(f"Body: {p['selftext']}")
        for j, c in enumerate(p['comments'][:8], 1):
            parts.append(f"  [comment, score {c['score']}]: {c['body']}")
    bundle = '\n'.join(parts)[:60000]
    return call_claude(SYS_PROMPT, bundle)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slug', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--aliases', required=True, help='comma-separated search terms')
    args = ap.parse_args()

    aliases = [a.strip() for a in args.aliases.split(',') if a.strip()]
    posts = harvest(args.slug, args.name, aliases)

    raw_path = os.path.join(OUT_DIR, f'{args.slug}.json')
    with open(raw_path, 'w') as f:
        json.dump({
            'slug': args.slug, 'name': args.name, 'aliases': aliases,
            'harvestedAt': time.strftime('%Y-%m-%d'),
            'postCount': len(posts), 'posts': posts,
        }, f, indent=2)
    print(f'  raw -> {raw_path}')

    summary, err = summarize(args.slug, args.name, posts)
    if err:
        print(f'  summary error: {err}')
        return 1
    # Strip CLI preamble: drop everything before the first "## " heading.
    idx = summary.find('\n## ')
    if idx < 0:
        idx = summary.find('## ')
        start = idx if idx >= 0 and summary[idx] == '#' else (idx + 1 if idx >= 0 else 0)
    else:
        start = idx + 1
    summary = summary[start:].lstrip() if start else summary
    summary_path = os.path.join(OUT_DIR, f'{args.slug}.md')
    with open(summary_path, 'w') as f:
        f.write(summary + '\n')
    words = len(summary.split())
    print(f'  summary ({words} words) -> {summary_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
