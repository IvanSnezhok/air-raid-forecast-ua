#!/usr/bin/env bash
# Check out / commit the `live-data` branch (point-in-time store) as a worktree in $STORE.
#   live-data.sh checkout          worktree at $STORE (orphan branch on the first run)
#   live-data.sh commit "message"  commit $STORE and push, rebasing on concurrent pushes
#
# STORE_DAYS=N (the 10-minute pipeline): fetch only the tip commit, without file
# contents, and check out only _state/ and the day partitions of the last N days
# (plus tomorrow, for a run that crosses midnight UTC). The store grows by every
# run and is never rewritten, so a full checkout would get slower every day; the
# pipeline reads at most ~10 days back. Without STORE_DAYS (daily history pull)
# the full tree is checked out.
set -euo pipefail
STORE="${STORE:-live-data}"
BRANCH=live-data
KINDS="polls alerts_active alerts_history history_pulls forecasts verification"

case "$1" in
  checkout)
    if [ -n "${STORE_DAYS:-}" ]; then
      git fetch -q --depth=1 --filter=blob:none origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" || true
    else
      git fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" || true
    fi
    if ! git rev-parse --verify -q "origin/$BRANCH" >/dev/null; then
      git worktree add -q --orphan -b "$BRANCH" "$STORE"
    elif [ -n "${STORE_DAYS:-}" ]; then
      git worktree add -q --no-checkout -B "$BRANCH" "$STORE" "origin/$BRANCH"
      dirs="_state"
      for day in $(date -u -d tomorrow +%F) $(for d in $(seq 0 "$STORE_DAYS"); do date -u -d "$d days ago" +%F; done); do
        for k in $KINDS; do dirs="$dirs $k/$day"; done
      done
      # shellcheck disable=SC2086
      git -C "$STORE" sparse-checkout set --cone $dirs
      git -C "$STORE" checkout -q "$BRANCH"
      echo "store: $(find "$STORE" -name '*.parquet' | wc -l) parquet files of the last $STORE_DAYS days checked out"
    else
      git worktree add -q -B "$BRANCH" "$STORE" "origin/$BRANCH"
    fi
    ;;
  commit)
    cd "$STORE"
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add --sparse -A .
    git diff --cached --quiet && { echo "nothing to commit"; exit 0; }
    git commit -q -m "$2"
    # Data files are immutable and uniquely named, and each writer owns its _state/
    # file, so a rebase only ever has to stack our new files on top of what another
    # job pushed meanwhile.
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
