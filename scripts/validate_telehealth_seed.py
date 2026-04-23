#!/usr/bin/env python3
"""Validate each seed brand's domain responds. Drops dead brands; keeps working ones.
Writes data/telehealth-seed-validated.json.
"""
import json, os, sys
import urllib.request, urllib.error
import socket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, 'data', 'telehealth-seed.json')
OUT = os.path.join(ROOT, 'data', 'telehealth-seed-validated.json')

UA = "Mozilla/5.0 (compatible; TRTIndexBot/1.0; +https://trtindex.com)"

def probe(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, url
    except (urllib.error.URLError, socket.timeout, Exception) as e:
        return None, str(e)

def main():
    with open(SEED) as f:
        brands = json.load(f)
    alive, dead = [], []
    for b in brands:
        status, final = probe(b['website'])
        ok = status in (200, 301, 302, 403)
        tag = 'OK' if ok else 'DEAD'
        print(f"  {tag:4s} {b['slug']:30s} -> {status} {final[:80] if isinstance(final, str) else ''}")
        if ok:
            if isinstance(final, str) and final.startswith('http'):
                b['website'] = final.rstrip('/')
            alive.append(b)
        else:
            dead.append({**b, 'error': str(final)})
    with open(OUT, 'w') as f:
        json.dump(alive, f, indent=2)
    print(f"\n{len(alive)} alive, {len(dead)} dead -> {OUT}")
    if dead:
        print("dead:")
        for d in dead:
            print(f"  - {d['slug']}: {d.get('error', 'n/a')[:80]}")

if __name__ == '__main__':
    main()
