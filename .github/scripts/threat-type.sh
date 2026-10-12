#!/usr/bin/env bash
# Threat-type forecast for the pipeline, run AFTER predict_now.py: it multiplies its
# P(type | onset) by the main forecast's P(onset) from reports/forecast_now.csv and
# uses the same origin (reports/forecast_meta.json).
# Posts: the Air Force channel in the text-data archive (just updated by the text
# collector, so no second fetch); if that checkout is missing, the history on the
# `threat-labels-data` branch (only its one parquet blob is downloaded) plus the
# newest posts scraped by the script.
set -euo pipefail
if [ -d text-data/telegram/kpszsu ]; then
  python threat-type/predict_type_now.py --store text-data --no-fetch
else
  posts="${RUNNER_TEMP:-/tmp}/tg_kpszsu.parquet"
  git fetch -q --depth=1 --filter=blob:none origin "+refs/heads/threat-labels-data:refs/remotes/origin/threat-labels-data"
  git show origin/threat-labels-data:tg_kpszsu.parquet > "$posts"
  python threat-type/predict_type_now.py --posts "$posts"
fi
