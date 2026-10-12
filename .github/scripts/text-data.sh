#!/usr/bin/env bash
# The `text-data` archive (public Telegram channels, news; src/airraid/text/) in the
# pipeline, which is its one writer every 10 minutes (text-collect.yml runs by hand only).
#   text-data.sh checkout check out text-data (shallow) only (train job)
#   text-data.sh update   check out text-data and fetch what is new (pipeline, before the forecast)
#   text-data.sh commit   commit and push it (rebasing on concurrent pushes)
set -euo pipefail
checkout() {
  [ -d text-data ] && return 0
  if git fetch -q --depth=1 origin "+refs/heads/text-data:refs/remotes/origin/text-data"; then
    git worktree add -q -B text-data text-data origin/text-data
  elif ! git ls-remote --exit-code --heads origin text-data >/dev/null; then
    git worktree add -q --orphan -b text-data text-data  # first run: start the archive
  else
    echo "could not fetch text-data" >&2; exit 1  # never start a second, unrelated history
  fi
}
case "$1" in
  checkout)
    checkout
    ;;
  update)
    checkout
    PYTHONPATH=src python -m airraid.text.collect update --root text-data
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
