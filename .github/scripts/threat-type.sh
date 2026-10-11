#!/usr/bin/env bash
# Threat-type forecast for the pipeline, run AFTER predict_now.py: it multiplies its
# P(type | onset) by the main forecast's P(onset) from reports/forecast_now.csv and
# uses the same origin (reports/forecast_meta.json).
# Posts: the Air Force channel history on the `threat-labels-data` branch (only the
# one parquet blob is downloaded), plus the newest posts scraped by the script.
set -euo pipefail
posts="${RUNNER_TEMP:-/tmp}/tg_kpszsu.parquet"
git fetch -q --depth=1 --filter=blob:none origin "+refs/heads/threat-labels-data:refs/remotes/origin/threat-labels-data"
git show origin/threat-labels-data:tg_kpszsu.parquet > "$posts"
# [hook: PR 3, text signals] once the text-data archive is checked out (text-data/), the
# script can read and archive the posts there instead: --store text-data
if [ -d text-data/telegram ] && grep -q -- "--store" threat-type/predict_type_now.py; then
  python threat-type/predict_type_now.py --store text-data
else
  python threat-type/predict_type_now.py --posts "$posts"
fi
