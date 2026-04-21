#!/usr/bin/env bash
# overnight_run.sh — sequential orchestrator for tonight's scraping pass.
#
# Order matters (all three scripts write to the same clinics.min.json, so we
# serialize): chains first (fast, known-good), then Places phrase expansion
# (medium), then Biote (slow multi-level walk), then classifier to cover all
# newly-added records.

set -u  # not -e: we want to continue even if one stage hits a hiccup
cd "$(dirname "$0")/.." || exit 2

LOG_DIR=logs
mkdir -p "$LOG_DIR"
STAMP=$(date -u +'%Y%m%dT%H%M%SZ')
MASTER_LOG="$LOG_DIR/overnight_${STAMP}.log"

echo "=== overnight run started $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===" | tee -a "$MASTER_LOG"

backup_data() {
  cp public/data/clinics.min.json "$LOG_DIR/clinics.min.${STAMP}.bak.json" 2>/dev/null || true
}

run_stage() {
  local label="$1"; shift
  local log="$LOG_DIR/${label}_${STAMP}.log"
  echo "---- stage: $label ---- $(date -u +'%Y-%m-%dT%H:%M:%SZ')" | tee -a "$MASTER_LOG"
  echo "cmd: $*" | tee -a "$MASTER_LOG"
  backup_data
  if "$@" >>"$log" 2>&1; then
    echo "  [ok] $label (log: $log)" | tee -a "$MASTER_LOG"
  else
    rc=$?
    echo "  [FAIL rc=$rc] $label (log: $log)" | tee -a "$MASTER_LOG"
  fi
  tail -30 "$log" | sed 's/^/    /' | tee -a "$MASTER_LOG"
}

# Stage 1: run only the NEW chains (restore, serotonin). The previously-run
# chains (gameday, lowt, renewvit) are idempotent but slow to re-check — skip
# unless we need to.
run_stage chains python3 scripts/scrape_chains.py --chains restore,serotonin

# Stage 2: Places phrase expansion — only the 6 new phrases, statewide.
run_stage places python3 scripts/scrape_places.py \
  --mode statewide \
  --queries "bhrt clinic,bioidentical hormone clinic,peptide therapy clinic,andropause clinic,age management medicine,mens wellness clinic" \
  --max-pages 2

# Stage 3: Biote (two-phase enumerate + unmatched detail-fetch).
run_stage biote python3 scripts/scrape_biote.py --resume

# Stage 4: classifier on all new records (resumable via classificationAt).
run_stage classify python3 scripts/enrich_clinics.py

echo "=== overnight run finished $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===" | tee -a "$MASTER_LOG"
