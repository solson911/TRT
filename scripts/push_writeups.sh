#!/usr/bin/env bash
# Periodic commit+push of newly-generated clinic writeups. Runs while the
# summarize_clinics.py batch is live so writeups ship to prod as they land
# (Cloudflare Pages rebuilds on each push).
#
# - Only touches data/clinic_writeups/ - will not pick up unrelated edits.
# - Skips cleanly when there's nothing new to ship.
# - Stops if anything is half-staged for a different commit (paranoia guard).
#
# Usage:
#   nohup scripts/push_writeups.sh > logs/push_writeups.log 2>&1 &
#
# Env:
#   INTERVAL_SECONDS  - default 7200 (2h)

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$REPO"

INTERVAL="${INTERVAL_SECONDS:-7200}"
BRANCH="main"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

log "push loop started; repo=$REPO interval=${INTERVAL}s"

while true; do
  sleep "$INTERVAL"

  # Abort if someone else has staged work - we only want to ship writeups.
  if ! git diff --cached --quiet; then
    log "aborting iteration: index already has staged changes (not touching)"
    continue
  fi

  # Count untracked + modified writeup files.
  PENDING=$(git status --porcelain -- data/clinic_writeups/ | wc -l | tr -d ' ')
  if [ "$PENDING" -eq 0 ]; then
    log "no new writeups; skipping"
    continue
  fi

  git add data/clinic_writeups/ 2>&1
  STAGED=$(git diff --cached --name-only -- data/clinic_writeups/ | wc -l | tr -d ' ')
  if [ "$STAGED" -eq 0 ]; then
    log "staged 0 writeups after add; skipping"
    continue
  fi

  TOTAL=$(find data/clinic_writeups -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')
  MSG="Add $STAGED clinic writeups (running total: $TOTAL)"

  if ! git commit -m "$MSG

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>" > /tmp/pw_commit.log 2>&1; then
    log "commit failed:"
    tail -5 /tmp/pw_commit.log
    git reset HEAD -- data/clinic_writeups/ 2>/dev/null
    continue
  fi

  if git push origin "$BRANCH" > /tmp/pw_push.log 2>&1; then
    log "pushed $STAGED writeups (total=$TOTAL)"
  else
    log "push failed:"
    tail -5 /tmp/pw_push.log
  fi
done
