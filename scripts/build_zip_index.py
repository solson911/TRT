#!/usr/bin/env python3
"""
Build a compact zip-code -> [lat, lng] index from the US Census Gazetteer
(public domain). Output: public/zip-index.json

Source: 2024 ZCTA national gazetteer
  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/

Usage:
  python3 scripts/build_zip_index.py
"""
import io, json, os, sys, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'public', 'zip-index.json')
URL = 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_zcta_national.zip'


def main():
    print(f'fetching {URL}')
    with urllib.request.urlopen(URL, timeout=60) as r:
        blob = r.read()
    zf = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in zf.namelist() if n.endswith('.txt'))
    raw = zf.read(name).decode('utf-8', errors='replace')
    lines = raw.splitlines()
    header = lines[0].split('\t')
    # Headers in the gazetteer include surrounding whitespace; strip.
    header = [h.strip() for h in header]
    idx_geoid = header.index('GEOID')
    idx_lat = header.index('INTPTLAT')
    idx_lon = header.index('INTPTLONG')

    zips = {}
    for line in lines[1:]:
        cols = line.split('\t')
        if len(cols) <= idx_lon:
            continue
        zip5 = cols[idx_geoid].strip()
        try:
            lat = round(float(cols[idx_lat].strip()), 4)
            lon = round(float(cols[idx_lon].strip()), 4)
        except ValueError:
            continue
        if len(zip5) != 5 or not zip5.isdigit():
            continue
        zips[zip5] = [lat, lon]

    out = json.dumps(zips, separators=(',', ':'))
    with open(OUT, 'w') as f:
        f.write(out)
    print(f'wrote {OUT}: {len(zips)} zips, {len(out)} bytes')


if __name__ == '__main__':
    main()
