#!/usr/bin/env bash
# nightly.sh - discovery + enrichment + deploy, safe to re-run and cron-friendly.
#
# Pipeline:
#   1. Backup current data/clinics.min.json
#   2. Discovery:
#        - Known chains (free, fast)
#        - Google Places light pass (statewide, 2 high-signal queries, 1 page)
#   3. Classify any new/unclassified records via enrich_clinics
#   4. Enrich new directory-eligible clinics:
#        - fetch favicon   (Google S2, free)
#        - fetch pages     (scrape homepage + services/about)
#        - summarize       (claude CLI, haiku)
#   5. Commit and push if data/public changed, so Cloudflare Pages auto-deploys
#
# Each downstream script is idempotent and skips anything already processed,
# so a nightly run costs ~nothing on days that surface zero new clinics.
#
# Cron suggestion (3:07 AM local):
#   7 3 * * * /home/claw/.openclaw/workspace/projects/trt-clinics/scripts/nightly.sh
#
# Env requirements:
#   PLACES_UNRESTRICTED_API_KEY  (sourced from project .env if present)
#   claude CLI on $PATH for summarization
#   git configured with push access to the main branch

set -u
cd "$(dirname "$0")/.." || exit 2
ROOT="$(pwd)"

# Single-instance lock. Summarization is slow and we don't want two runs
# fighting over claude CLI, git, and the same data files.
LOCK="$ROOT/logs/nightly.lock"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "nightly already running (lock $LOCK); exiting"
  exit 0
fi

# Load .env if present so PLACES_UNRESTRICTED_API_KEY etc. are available
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
STAMP=$(date -u +'%Y%m%dT%H%M%SZ')
MASTER_LOG="$LOG_DIR/nightly_${STAMP}.log"
BACKUP="$LOG_DIR/clinics.min.${STAMP}.bak.json"

log() { echo "[$(date -u +'%H:%M:%SZ')] $*" | tee -a "$MASTER_LOG"; }

count_eligible() {
  python3 - <<'PY'
import json
with open('data/clinics.min.json') as f:
    d = json.load(f)
a = d if isinstance(d, list) else d.get('clinics', [])
print(sum(1 for c in a if c.get('classification') in ('primary_trt','offers_trt') and not c.get('telehealth')))
PY
}

count_total() {
  python3 - <<'PY'
import json
with open('data/clinics.min.json') as f:
    d = json.load(f)
a = d if isinstance(d, list) else d.get('clinics', [])
print(len(a))
PY
}

run_stage() {
  local label="$1"; shift
  local log="$LOG_DIR/${label}_${STAMP}.log"
  log "---- stage: $label"
  log "cmd: $*"
  if "$@" >>"$log" 2>&1; then
    log "  [ok] $label (log: $log)"
  else
    log "  [FAIL rc=$?] $label (log: $log)"
  fi
  tail -10 "$log" | sed 's/^/    /' >> "$MASTER_LOG"
}

log "=== nightly run starting ==="
log "repo: $ROOT"

cp data/clinics.min.json "$BACKUP" 2>/dev/null || log "no clinics.min.json to back up"
BEFORE_TOTAL=$(count_total)
BEFORE_ELIG=$(count_eligible)
log "starting records: total=$BEFORE_TOTAL eligible=$BEFORE_ELIG"

# --- 1. Discovery (cheap nightly pass) ---

# Known chains: free, finds new locations of chains we already track.
run_stage chains python3 scripts/scrape_chains.py

# Google Places: 2 highest-signal queries, one page each, statewide.
# A full phrase x metro sweep belongs on a weekly/manual cadence, not nightly.
run_stage places python3 scripts/scrape_places.py \
  --mode statewide \
  --queries "trt clinic,testosterone replacement therapy" \
  --max-pages 1

AFTER_DISCOVERY_TOTAL=$(count_total)
NEW_RAW=$((AFTER_DISCOVERY_TOTAL - BEFORE_TOTAL))
log "discovery added $NEW_RAW raw records"

# --- 2. Classification ---

run_stage classify python3 scripts/enrich_clinics.py

AFTER_CLASSIFY_ELIG=$(count_eligible)
NEW_ELIG=$((AFTER_CLASSIFY_ELIG - BEFORE_ELIG))
log "classification marked $NEW_ELIG new directory-eligible clinics"

# --- 3. Enrichment (only missing items actually get work) ---

run_stage favicons python3 scripts/fetch_clinic_favicons.py --sleep 0.15
run_stage pages python3 scripts/fetch_clinic_pages.py --sleep 0.6
run_stage summarize python3 scripts/summarize_clinics.py

# --- 4. Commit and push if anything changed ---

cd "$ROOT"
if git status --porcelain | grep -qE '^(A|M|\?\?) (data/|public/img/)'; then
  log "changes detected, committing"
  git add data/clinics.min.json \
          "data/clinic_pages" \
          "data/clinic_writeups" \
          "public/img/clinics" 2>/dev/null || true
  MSG="Nightly: +${NEW_RAW} raw, +${NEW_ELIG} eligible ($(date -u +'%Y-%m-%d'))"
  if git diff --cached --quiet; then
    log "nothing staged after filtering, skipping commit"
  else
    git commit -m "$MSG" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>" >>"$MASTER_LOG" 2>&1
    git push >>"$MASTER_LOG" 2>&1 && log "pushed to origin" || log "push FAILED (see log)"
  fi
else
  log "no data changes, skipping commit"
fi

log "=== nightly run finished ==="
log "final records: total=$(count_total) eligible=$(count_eligible)"
