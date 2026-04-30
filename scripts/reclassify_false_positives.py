#!/usr/bin/env python3
"""
reclassify_false_positives.py - flip peptide/IV/aesthetic false-positives from
offers_trt to unrelated via a rule-based sweep, without re-calling the LLM.

Criteria to flip:
  - current classification is 'offers_trt' (high-confidence primary_trt records
    are left alone - those have strong name signals).
  - name matches peptide therapy, IV hydration, med-spa, aesthetic, or
    weight-loss-only patterns.
  - classification reason does NOT mention testosterone/TRT/HRT/hormone
    (i.e., the original classifier couldn't cite an actual TRT signal).

Writes back to data/clinics.min.json in place. Prints a summary and a sample
of flipped records.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.clinics_io import load_all, save_all  # noqa: E402

NAME_PATTERN = re.compile(
    r"\b(peptide(?:s)?(?:\s+therapy)?|iv\s+(?:hydration|therapy|lounge|bar|drip)"
    r"|med\s*spa|medspa|medical\s+spa|aesthetic(?:s)?|cosmetic\s+(?:surgery|clinic)"
    r"|botox|weight\s*loss(?:\s+clinic)?)\b",
    re.IGNORECASE,
)

# If the reason mentions any of these, trust it had a real TRT signal.
TRT_SIGNAL_IN_REASON = re.compile(
    r"\b(testosterone|trt|hrt|hormone\s+replacement|low[\s-]*t|men'?s\s+health"
    r"|andropause|hypogonad)\b",
    re.IGNORECASE,
)


def should_flip(c):
    if c.get('classification') != 'offers_trt':
        return False
    name = c.get('name') or ''
    if not NAME_PATTERN.search(name):
        return False
    reason = c.get('classificationReason') or ''
    if TRT_SIGNAL_IN_REASON.search(reason):
        return False
    # Website text often has "trt" in URL when relevant; don't flip if website
    # domain itself references testosterone.
    website = (c.get('website') or '').lower()
    if re.search(r'(testosterone|trt|lowt|mens-?health|hormone)', website):
        return False
    return True


def main():
    clinics = load_all()

    flipped = []
    for c in clinics:
        if should_flip(c):
            c['classification'] = 'unrelated'
            c['classificationConfidence'] = 'high'
            prior = c.get('classificationReason') or ''
            c['classificationReason'] = (
                f"rule-flip: name matches peptide/IV/medspa/aesthetic/weight-loss "
                f"with no TRT signal in original reason ('{prior[:60]}')"
            )
            flipped.append(c)

    save_all(clinics)

    print(f"flipped {len(flipped)} records from offers_trt -> unrelated")
    print()
    print("sample:")
    for c in flipped[:15]:
        print(f"  - {c.get('name')[:60]:60s}  {c.get('city', '?')}, {c.get('state', '?')}")
    if len(flipped) > 15:
        print(f"  ... and {len(flipped) - 15} more")


if __name__ == '__main__':
    main()
