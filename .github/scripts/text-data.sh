#!/usr/bin/env bash
# [hook: PR 3, text signals] Update the `text-data` archive (public Telegram channels,
# news) in the pipeline, once `python -m airraid.text.collect` exists (src/airraid/text/).
# Runs only when that module is present; the exact command is reconciled when PR 3 lands.
#   text-data.sh update   check out text-data (shallow) and fetch what is new
#   text-data.sh commit   commit and push it (rebasing on concurrent pushes)
set -euo pipefail
[ -f src/airraid/text/collect.py ] || { echo "text collector not in this checkout: skipped"; exit 0; }
case "$1" in
  update)
    if [ ! -d text-data ]; then
      if git fetch -q --depth=1 origin "+refs/heads/text-data:refs/remotes/origin/text-data"; then
        git worktree add -q -B text-data text-data origin/text-data
      else
        git worktree add -q --orphan -b text-data text-data
      fi
    fi
    python -m airraid.text.collect update --root text-data
    ;;
  commit)
    [ -d text-data ] || exit 0
    cd text-data
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add -A .
    git diff --cached --quiet && { echo "text-data: nothing to commit"; exit 0; }
    git commit -q -m "text signals $(date -u +%FT%H:%MZ) (pipeline)"
    for i in 1 2 3 4; do
      git push -q origin HEAD:text-data && exit 0
      sleep $((i * 3)); git pull -q --rebase origin text-data || { git rebase --abort || true; }
    done
    exit 1
    ;;
esac
