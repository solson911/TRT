#!/usr/bin/env python3
"""Wrapper that forwards to peptide-site's multi-tenant gen-news-images.py
with --project trtindex injected.

The News Board server invokes `python3 <pipeline>/gen-news-images.py <draft.md>`
without project flags, so each tenant needs its own script at
<pipeline>/gen-news-images.py. The real logic lives in peptide-site (it loads
tenant config via project_config.py from News Board project JSON). This wrapper
just makes sure --project trtindex is present so paths/dimensions/format come
from projects/trtindex.json, not peptide defaults.
"""
import os
import sys

PEPTIDE_GEN = "/home/claw/.openclaw/workspace/projects/peptide-site/news-pipeline/gen-news-images.py"

if not os.path.isfile(PEPTIDE_GEN):
    sys.stderr.write(f"upstream gen-news-images.py not found at {PEPTIDE_GEN}\n")
    sys.exit(2)

argv = list(sys.argv[1:])
if "--project" not in argv:
    argv = ["--project", "trtindex", *argv]

os.execv(sys.executable, [sys.executable, PEPTIDE_GEN, *argv])
