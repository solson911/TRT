#!/usr/bin/env bash
# git_push.sh — push the current branch using GITHUB_TOKEN from .env.
#
# Usage:
#   scripts/git_push.sh                  # push current branch
#   scripts/git_push.sh feature-branch   # push specific branch
#
# The token is injected inline and never written to .git/config, so it
# stays isolated to .env (gitignored). Intended for automation scripts
# that commit data/content refreshes and push unattended.

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "error: .env not found (expected GITHUB_TOKEN)" >&2
  exit 2
fi

# Load .env without leaking to process list
set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "error: GITHUB_TOKEN is empty in .env" >&2
  exit 2
fi

branch="${1:-$(git symbolic-ref --short HEAD)}"
remote_url=$(git config --get remote.origin.url)

if [[ "$remote_url" != https://github.com/* ]]; then
  echo "error: origin is not an https://github.com URL: $remote_url" >&2
  exit 2
fi

# Strip any embedded creds, then inject x-access-token for this push only
clean_url="${remote_url#https://}"
clean_url="https://${clean_url#*@}"
auth_url="https://x-access-token:${GITHUB_TOKEN}@${clean_url#https://}"

git push "$auth_url" "$branch" 2>&1 | sed "s|x-access-token:[^@]*@|x-access-token:[REDACTED]@|g"
