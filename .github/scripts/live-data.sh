#!/usr/bin/env bash
# Check out / commit the `live-data` branch (point-in-time store) as a worktree in $STORE.
#   live-data.sh checkout          worktree at $STORE (orphan branch on the first run)
#   live-data.sh commit "message"  commit $STORE and push, rebasing on concurrent pushes
set -euo pipefail
STORE="${STORE:-live-data}"
BRANCH=live-data

case "$1" in
  checkout)
    git fetch -q origin "$BRANCH" || true
    if git rev-parse --verify -q "origin/$BRANCH" >/dev/null; then
      git worktree add -q -B "$BRANCH" "$STORE" "origin/$BRANCH"
    else
      git worktree add -q --orphan -b "$BRANCH" "$STORE"
    fi
    ;;
  commit)
    cd "$STORE"
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add -A .
    git diff --cached --quiet && { echo "nothing to commit"; exit 0; }
    git commit -q -m "$2"
    # Data files are immutable and uniquely named, so a rebase only ever has to
    # stack our new files on top of what another job pushed meanwhile.
    for i in 1 2 3 4 5 6; do
      if git push -q origin "HEAD:$BRANCH"; then echo "pushed: $2"; exit 0; fi
      sleep $((i * 3))
      git pull -q --rebase origin "$BRANCH" || { git rebase --abort || true; }
    done
    echo "push failed after retries" >&2
    exit 1
    ;;
  *)
    echo "usage: $0 checkout|commit MESSAGE" >&2; exit 2 ;;
esac
