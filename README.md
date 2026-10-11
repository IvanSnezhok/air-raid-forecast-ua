# Air raid alert forecasting for Ukraine

Per-region probabilistic forecasts of air raid alerts for the next 1–6 hours, refreshed
**every 10 minutes** (see [Automation](#automation)), built from the full alert history
since February 2022 and evaluated on the **latest four weeks** (12 Sep – 11 Oct 2026)
with a strict, leak-free walk-forward protocol.

Two questions are answered for each of 24 regions (23 oblasts + Kyiv City) at every
hour T:

| target | question | why |
|---|---|---|
| `active` | Will an alert be active at any moment of hour *T+h*? | what a person planning the next hours cares about |
| `onset`  | Will a **new** alert start within the next *h* hours? | harder and more useful: the "active" target is inflated by alerts that are already running |

## Quick start

```bash
pip install -r requirements.txt
python -m pytest            # leakage, verification, pipeline and web app tests (about 1.5 min)
python run.py               # full evaluation: model selection + 4-week walk-forward (about 30 min on 4 cores)
python run.py --reuse-selection   # skip model selection, reuse reports/selection.json
python predict_now.py       # live forecast for the next 6 hours (+ appends to the forecast log)
python verify.py            # score logged forecasts once their horizon has passed

# with the point-in-time store (a checkout of the `live-data` branch, see "Live data")
git worktree add live-data origin/live-data
python predict_now.py --store live-data   # fills the hours after the dataset, logs to the store
python verify.py --store live-data        # verifies within hours instead of a day later
python -m airraid.collect poll --root live-data      # what the pipeline runs every 10 min (needs the token)
python scripts/train_models.py --out var/models      # what the train job runs (the served model set)
```

`predict_now.py` and the collector read an alerts.in.ua token from the
`ALERTS_IN_UA_TOKEN` environment variable only. The token is never stored in the repo
or printed.

## Data

* **Source:** the volunteer (eTryvoga) file of
  [Vadimkin/ukrainian-air-raid-sirens-dataset](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset),
  116 k oblast-level alerts from 2022-02-25 to 2026-10-11 04:36 UTC.
* **Why not the "official" file from the same repo?** Checked before writing any code:
  1. it stopped updating on **2026-09-07**, so it cannot be used to evaluate on the latest period;
  2. since December 2025 most official alerts are declared per *raion* (district), so
     "an alert in the oblast" means something different before and after. The volunteer
     file is oblast-level for the whole war.
* **Cleaning** (`src/airraid/data.py`): Luhansk oblast is dropped (permanent alert since
  April 2022, almost no records) and Crimea is not in the data. Zero-length records are
  dropped. Alerts are capped at 12 hours: a few records last for days in western oblasts
  (177 h in Chernivtsi), which are missed "all clear" messages. The cap depends only on
  elapsed time, so it can be applied identically in real time. About 5 % of records have
  an imputed end (`naive=True`, start + 30 min); they are kept.

## Method

**Features** (`src/airraid/features.py`), all computed from what is known at T:

* current state: alert running now, how long it has been running, minutes since the
  last alert ended, alert starts in the last 6 h / 24 h / 7 d;
* own history: share of each of the last 24 hours under alert, share of the last
  24 h / 7 d / 28 d;
* national picture: number of regions under alert now, nationwide starts in the last
  1 h / 3 h, national share of the last 24 h;
* spatial picture: whether *each* of the 24 regions is under alert now and was in the
  last hour. The model learns which regions lead which, instead of a hand-written
  neighbour map;
* calendar: Kyiv local hour and day of week of the target hour; same-hour climatology
  over the last 7 / 28 days.

**Models** (`src/airraid/models.py`), one per target and horizon, pooled over regions:

| model | what it is |
|---|---|
| `climatology_28d` | share of the last 28 days with an alert (or onset) in the same hour(s) of the day. Reference for skill scores |
| `persistence` | 0/1: alert running now (`active`) / alert started in the last h hours (`onset`) |
| `markov_persistence` | P(event \| region, alert running now), fitted on the training window |
| `logistic_regression` | all features, trained on the last 12 months |
| `lightgbm_no_spatial` | gradient boosting without the national/spatial features (ablation) |
| `lightgbm` | gradient boosting, all features |

**Evaluation protocol** (`run.py`):

```
 ... training history (2022-03 → ) ... | validation 28 d | test 28 d (4 weekly folds) |
                                         2026-08-15       2026-09-12     2026-10-10 23:00 UTC
```

1. *Model selection* on the validation month: training window (3 / 6 / 12 / 24 months /
   all) and number of trees (early stopping) per target and horizon. The test month is
   not touched.
2. *Walk-forward test* on the last 28 days: four weekly folds; before each fold every
   model is retrained on data up to the fold start, as it would be in production.
3. *Embargo*: training origins stop 6 hours before the first evaluated origin, so no
   training label overlaps the evaluated period.
4. Metrics: Brier score, Brier skill score vs climatology, log loss, ROC-AUC, PR-AUC,
   reliability diagrams, per-region skill.

## How leakage is prevented, and tested

* **Construction:** every feature at T uses minutes strictly before T, plus the fact
  that an alert is running at T and how long it has run. The end time of an alert that
  is still running at T is never used.
* **Mechanical test** (`tests/test_leakage.py`): for random origins T the pipeline is
  rebuilt from data *truncated at T*, as if running live, and every feature must match
  the features computed from the full data. I checked that the test bites by injecting
  two leaks: a lag-0 feature, and "minutes since last alert ended" using the end of the
  running alert. Both made the test fail.
* **Sub-hour origins** (the 10-minute pipeline forecasts from any minute): the same
  truncation test at :10/:23/:40/:50 origins, a check that the hour blocks are exactly
  the minutes [T−60, T) and [T, T+h), and a mutation test that a feature reading the end
  of the running alert makes it fail (see "Automation").
* **Label test:** wiping all history before T must not change the labels.
* **Label-not-a-feature test + guard:** see "What went wrong" below.

## Results (latest 4 weeks, walk-forward)

Test: forecast origins 2026-09-12 23:00 → 2026-10-10 23:00 UTC, 24 regions, about 16 k
forecasts per target and horizon. Brier score (lower is better); in brackets, the skill
vs the 28-day climatology (1 − Brier/Brier_clim). Full tables:
[`reports/metrics_test.md`](reports/metrics_test.md).

**Target `active`** (alert at any moment of hour T+h; base rate 33 %)

| model | h = 1 | h = 2 | h = 3 | h = 6 |
|---|---|---|---|---|
| climatology_28d | 0.184 | 0.184 | 0.183 | 0.183 |
| persistence | 0.212 (−16 %) | 0.327 (−78 %) | 0.337 (−84 %) | 0.339 (−85 %) |
| markov_persistence | 0.161 (+12 %) | 0.205 (−11 %) | 0.214 (−17 %) | 0.200 (−9 %) |
| logistic_regression | 0.135 (+27 %) | 0.180 (+2 %) | 0.181 (+2 %) | 0.181 (+1 %) |
| lightgbm_no_spatial | **0.132** (+28 %) | **0.176** (+4 %) | **0.178** (+3 %) | 0.179 (+2 %) |
| **lightgbm** | **0.132** (+28 %) | 0.177 (+4 %) | **0.178** (+3 %) | **0.179** (+2 %) |

**Target `onset`** (a new alert starts within h hours; base rate 23 % / 39 % / 50 % / 67 %)

| model | h = 1 | h = 2 | h = 3 | h = 6 |
|---|---|---|---|---|
| climatology_28d | 0.153 | 0.183 | 0.182 | 0.137 |
| persistence | 0.311 | 0.363 | 0.353 | 0.255 |
| markov_persistence | 0.178 | 0.240 | 0.256 | 0.230 |
| logistic_regression | 0.149 (+3 %) | 0.180 (+2 %) | 0.180 (+1 %) | 0.136 (+1 %) |
| lightgbm_no_spatial | 0.146 (+5 %) | **0.176** (+4 %) | 0.176 (+3 %) | 0.135 (+2 %) |
| **lightgbm** | **0.146** (+5 %) | **0.176** (+4 %) | **0.176** (+3 %) | **0.132** (+3 %) |

ROC-AUC of LightGBM: active 0.86 / 0.77 / 0.77 / 0.76, onset 0.78 / 0.80 / 0.81 / 0.86.

![skill by horizon](reports/figures/skill_by_horizon.png)
![reliability](reports/figures/reliability.png)
![last test week](reports/figures/last_week_example.png)

**What this means, honestly**

* LightGBM is the best model for every target and horizon, and its probabilities are
  well calibrated (reliability curves sit on the diagonal). LightGBM beat the logistic
  regression and a separately tested survival-hazard model by 0.002–0.005 Brier.
* The big win is only at **1 hour for `active`** (+28 % over climatology). Most of it
  comes from knowing what is happening *now*: an alert already running, how long it has
  lasted, how many alerts started this week.
* Beyond the next hour, and for predicting **new** alerts, the history of alerts alone
  adds only **2–5 % over a good climatology**. Alerts are driven by launches the
  history cannot see. The 28-day same-hour climatology already captures most of the
  predictable part: the night and early-morning drone waves and the regional pattern.
* Plain persistence is a poor probabilistic forecast, worse than climatology at every
  horizon. Calibrated "Markov" persistence helps only at 1 hour.
* **The spatial features (state of every other region) did not help**: the ablation
  without them scores the same. The national counters and the region's own history
  carry that information already.
* Top features by gain: onset climatology, alert starts in the last 7 days, hour-of-day
  climatology, alert running now, elapsed time of the running alert
  ([`reports/feature_importance_top30.csv`](reports/feature_importance_top30.csv)).
* **Pooled AUC flatters the model.** Much of it comes from telling high-alert regions
  from quiet ones. *Within* a region the onset-3h AUC is only 0.52–0.74 (median about
  0.6), and the skill over climatology ranges from −4 % (Zaporizhzhia) to +10 %
  (Ternopil). Zakarpattia had no onsets at all in the test weeks, so its skill score is
  undefined. See [`reports/metrics_by_region.csv`](reports/metrics_by_region.csv) and
  `reports/figures/skill_by_region.png`.

Validation-month model selection preferred long training windows (all history or
24 months) for almost every target. The 2022–2025 data still helps despite the 2026
regime shift: twice as many alerts, shorter alerts.

## Live data

### Which live records count as "an alert in the oblast"

The model is trained on the volunteer file. Live, alerts.in.ua reports alerts per oblast,
raion, hromada and city, and keeps some frontline raion/hromada alerts open for weeks
(on 2026-10-11: a Kharkivska hromada since 2026-03-10, two Dnipropetrovska hromadas since
2026-09-17). The live state must mean what it means in training, so the definition was
measured, not assumed (`scripts/live_definition_check.py`, using the official
raion-level file of the same upstream repo, Dec 2025 – Sep 2026, and the real
alerts.in.ua history kept in the store, Sep – Oct 2026):

* **Oblast-level records alone do not work.** Since December 2025 alerts are declared per
  raion: outside Kyiv City the official file has *no* oblast-level records, so 1 % of
  volunteer alert starts match one (3-minute window), while the volunteer file has 3–14
  alerts a day per frontline oblast.
* **The volunteer file is a Telegram channel replayed per oblast:** any "alert" post for
  any part of the oblast opens (or restarts) the oblast's alert, the next all-clear post
  for any part closes it (upstream `volunteer_etryvoga_processor.py`). Measured: 87 % of
  volunteer alert *ends* coincide with an official all-clear somewhere in the same oblast
  (97 % in Kharkivska), but only 49 % of *starts* with an official activation (the channel
  also posts threat notices that are not sirens). The real alerts.in.ua feed shows the same
  (Sep 12 – Oct 11 2026): 1 % of starts match an oblast-level record, 86 % of ends an
  all-clear of any level.
* **Rule used** (`airraid.live_alerts.oblast_intervals` / `model_alerts`): replay every
  `air_raid` record of any level through the same rules: the latest activation starts the
  oblast alert, an all-clear for any part ends it, and with no new activation it is over
  after **1 hour** (`LIVE_EXPIRY_HOURS`, never more than the 12 h cap of `load_alerts`).
  A month-old frontline record can therefore never make an oblast "active".

Rules compared on hourly origins, official file Dec 2025 – Sep 2026 (volunteer:
104 alerts/day, mean 0.56 h, alert running at 10.4 % of origins):

| rule | alerts/day | mean length | share of origins under alert | Cohen's κ vs volunteer |
|---|---|---|---|---|
| oblast-level records only | 2 | 0.97 h | 0.3 % | 0.03 |
| any record, 12 h cap (old merge + cap) | 362 | 1.99 h | 27.5 % | 0.46 |
| latest activation until all-clear, 12 h | 96 | 0.96 h | 16.3 % | 0.60 |
| **same, 1 h expiry (used)** | 96 | 0.57 h | 9.8 % | 0.52 |

κ alone favours a 12 h expiry, so the expiry was chosen by what matters: a LightGBM
trained on the volunteer file before the period, scored against volunteer labels with its
inputs rebuilt from each rule (`--model`; "state only": the running-alert features;
"all recent": every feature, as when the store fills a whole gap). Mean Brier over the
8 target × horizon pairs (volunteer inputs = the floor):

| period, source | volunteer inputs | oblast only | any record, 12 h | latest until clear, 12 h | **…, 1 h** |
|---|---|---|---|---|---|
| Jan–Apr 2026, official (selection) – state only | 0.1377 | 0.1455 | 0.1492 | 0.1442 | **0.1433** |
| Jan–Apr 2026, official (selection) – all recent | 0.1377 | 0.2664 | 0.1751 | 0.1585 | **0.1517** |
| May–Sep 2026, official – state only | 0.1399 | 0.1473 | 0.1528 | 0.1474 | **0.1458** |
| May–Sep 2026, official – all recent | 0.1399 | 0.2895 | 0.1849 | 0.1661 | **0.1601** |
| Sep 20 – Oct 11 2026, alerts.in.ua – state only | 0.1643 | 0.1726 | 0.1829 | 0.1776 | 0.1730 |
| Sep 20 – Oct 11 2026, alerts.in.ua – all recent | 0.1643 | 0.2694 | 0.2069 | 0.1921 | **0.1825** |

Expiries of 1, 2, 3, 6 and 12 h were tried. On the selection period 1 h was best for
"all recent" (0.1517 vs 0.1531 for 2 h) and within 0.0001 of the best (2 h) for "state
only"; it is the best of the five on both later periods. Two honest caveats: the live
rule is a noisy stand-in for the volunteer channel (active-1h Brier 0.19 vs 0.14 with
the true volunteer state on the real feed), and on the real feed "state only" ties with
"assume calm" (oblast only), so most of the gain comes from filling the gap. The volunteer
file restarts an alert at the *last* post before the all-clear, which is only known after
the fact; live, the rule can only use the last activation before the forecast time.

### Point-in-time store (`src/airraid/store.py`, branch `live-data`)

Everything the live system sees or says is kept, with the moment it became known, on the
orphan branch `live-data` (`.github/workflows/pipeline.yml`: a poll every 10 min;
`collect.yml`: the alerts.in.ua history once a day; the API only keeps ~30 days, so it
was backfilled on 2026-10-11 from 2026-09-11):

```
live-data/<kind>/<YYYY-MM-DD>/<stamp>-<hash>.parquet   one immutable file per append (UTC day)
  polls           observed_at, status, n_records, levels     one row per active.json poll
  alerts_active   every record of every poll, all levels and alert types, observed_at
  alerts_history  month_ago records, fetched_at; deduplicated on append
  history_pulls   fetched_at, uid, status                    one row per history request
  forecasts       origin, issued_at, region, target, horizon, p, clim, active_now,
                  trained_until, hist_end, git_sha, live
  verification    scored forecasts, verified_at, label_source
live-data/_state/history.json   collector bookkeeping (resume, If-Modified-Since)
live-data/_state/pipeline.json  the last pipeline run: time, step timings, data ends (= the site's api/status.json)
```

`store.load(root, kind, as_of=T)` returns only rows known at T (`observed_at` /
`fetched_at` / `issued_at` ≤ T); for `alerts_history` it resolves each record (uid,
start, type) to the version known at T, with `first_seen_at` and `known_at` (when its end
became known). Files are never rewritten, so git grows only by new rows.

### What is and is not leak-free

* **Training never sees live data.** Models are fit on the volunteer file only, on origins
  whose label window it fully covers (`airraid.training`, `predict_now.grids`). Before this fix the training
  grid included the live records: frontline oblasts got weeks of fake "alert running, no
  new alert" rows, and the hourly run forecast `active` 0.999 / `onset` 0.000 for 6 h in
  Dnipropetrovska and Kharkivska, which have about 20 alerts a day.
* **Prediction features at origin T** may use, besides the dataset: store rows known at T
  (`first_seen_at`, `observed_at` ≤ T; an end is used only if it is ≤ T and was known at
  T) and a fresh poll's records that started before T (their disappearance after T is not
  used). They are added only after the dataset ends and never overlap a dataset record
  (`live_alerts.after_dataset`), so live data cannot rewrite history.
* **Not leak-free, by design:** verification labels. `verify.py` looks up outcomes after the
  fact (that is its job); the probability it scores is the one logged at issue time.
* **Approximate:** store-based labels (`label_source = "store"`) and gap features come from
  the live rule above, not from the volunteer channel; alerts that start and end between
  two polls are missing until the daily history pull. Dataset labels replace store
  labels once the dataset covers the window.

## Learning loop (live)

* `predict_now.py` forecasts the next 6 hours for every region and appends each forecast
  to `reports/forecast_log.csv` and, with `--store`, to the store's `forecasts`. The
  origin is the first whole minute after the newest poll, so the 10-minute pipeline
  issues a new forecast every run (see "Sub-hour origins" below).
* **Retraining is a periodic refit**, not online learning: the train job
  (`.github/workflows/train.yml`, `scripts/train_models.py`) refits the models from
  scratch on the volunteer file as known at that moment, daily and whenever the dataset
  has 6 h more data than the served models; everything else only loads that bundle
  (`airraid.training`, `--models var/models`, not committed).
* `verify.py` looks up what actually happened once a forecast's horizon has passed and
  writes `reports/verification.csv` and `reports/verification_summary.json`: Brier of
  the model vs climatology over the last 24 h / 7 d / all time, hit rate, recall and
  false alarm ratio at p ≥ 0.5. With `--store` it reads the forecasts from the store,
  labels windows after the dataset end from the store's alerts.in.ua records (so within
  hours instead of a day later), and appends the result to the store. **Verification is
  monitoring:** it does not feed back into model weights. `tests/test_verify.py` checks
  that it uses exactly the training labels.
* `predict_now.py --as-of 2026-10-10T12:20` replays the live path at a past minute: data
  after it is dropped, store rows are used as they were known then, and models are
  retrained on the truncated dataset.

## Automation

Everything runs on GitHub Actions; nothing needs a laptop or a server.

```
 every 10 min (:03, :13, ...)   keep-alive / pinger / by hand
            │                              │
            ▼                              ▼
 pipeline.yml ── gate ── skip if the last update is < 5 min old (duplicate trigger)
            │
            ▼ run (one at a time: concurrency group, never cancelled)
   1 check out the store            live-data branch, tip only, last 10 days of partitions
   2 restore the served models      Actions cache  ◄──────────── train.yml (daily, on new data, by hand)
   3 download the volunteer dataset                                trains + holdout week, saves the bundle
   4 poll alerts.in.ua              → store: polls, alerts_active
   5 forecast (predict_now.py)      → store: forecasts        (trains once only if no compatible models)
   6 threat type (after 5)          multiplies P(onset) by P(type | onset), same origin
   7 verify (verify.py)             → store: verification     (24 h / 7 d Brier vs climatology, hit rate)
   8 build the static site          same models, forecast and verification (store mode, no training)
   9 status.json                    → site api/status.json + store _state/pipeline.json
  10 commit the store               new files only, rebase-and-retry against other writers
  11 dispatch train.yml             if the dataset has 6 h more data than the models
            │
            ├─ deploy ── GitHub Pages (main only)
            └─ keep-alive ── wait for the next slot + 3 min; if nothing started a newer run, dispatch one
 collect.yml (daily 02:41)  alerts.in.ua 30-day history → store (alongside the pipeline, own _state file)
```

| what | where it lives | written by |
|---|---|---|
| polls, active alerts, history pulls, forecasts, verification, run status | branch `live-data` (append-only parquet, `src/airraid/store.py`) | pipeline, collect-history |
| served model bundle (`airraid.training`) | Actions cache `models-v2-<code hash>-<data end>-<trained at>` | train.yml (pipeline only as a fallback) |
| the site (`api/*.json`, UI) | GitHub Pages | pipeline (deploy job) |
| Air Force posts (threat-type model) | branch `threat-labels-data` | threat-labels.yml |

**Cadence and timings.** A run starts every 10 minutes and takes about a minute; measured
on GitHub-hosted runners on 11 Oct 2026 (runs 2, 4 and 5 of the end-to-end test):

| step | seconds |
|---|---|
| setup: checkout, Python, packages (pip cache), model cache restore | 23–26 |
| check out the store (last 10 days, no blobs) | 0.5–0.9 |
| download the dataset / poll alerts.in.ua | 0.6–0.8 / 0.7–0.8 |
| forecast with the cached models | 3.3–3.5 |
| threat-type forecast (retrains its two small models) | 25–27 |
| verify | 1.8–1.9 |
| build the static site | 5.4–6.0 |
| **run job, start to store commit** | **67–72** |

The gate job takes 3–6 s. Without a model cache (first run, changed modelling code) the
forecast step trains the serving models itself in about 90 s; the train job (holdout
week + final refit) takes about 3.5 minutes. The forecast is logged about 3 s after the
poll and the site is built about 40 s after it.

**Sub-hour origins.** The forecast origin is the first whole minute after the newest poll
(`airraid.nowcast`), so every run forecasts "the next 60 minutes from 14:13", not the
14:00 forecast again. Every feature and label is defined on hour blocks of the minute
grid; `data.build_grid` shifts the grid start so that the blocks end exactly at the
origin (the lags at 14:13 are 13:13–14:13, …, hour 1 is 14:13–15:13), and the calendar
features use the Kyiv hour of the target block's midpoint (unchanged for top-of-hour
origins, so training is unchanged). Nothing after the origin is used: store rows known
at the origin (≤ origin) and records that started before it. The truncation test now
also runs at :10/:23/:40/:50 origins, an independent check confirms the blocks are the
minutes [T−60, T) and [T, T+h), and a mutation test confirms the check fails for a leaky
feature (`tests/test_leakage.py`). Models trained on top-of-hour origins stay calibrated
off the hour (`scripts/backtest_subhour.py`, every origin of 13 Sep – 11 Oct 2026 at each
offset, models trained on data before it):

| target | h | Brier :00 | Brier :20 | Brier :40 | skill :00 | skill :20 | skill :40 | ECE :00 | ECE :20 | ECE :40 |
|---|---|---|---|---|---|---|---|---|---|---|
| active | 1 | 0.1323 | 0.1308 | 0.1308 | +0.278 | +0.288 | +0.286 | 0.012 | 0.011 | 0.013 |
| active | 3 | 0.1781 | 0.1781 | 0.1775 | +0.027 | +0.030 | +0.031 | 0.014 | 0.012 | 0.016 |
| onset | 1 | 0.1467 | 0.1468 | 0.1465 | +0.044 | +0.047 | +0.046 | 0.013 | 0.011 | 0.012 |
| onset | 3 | 0.1751 | 0.1752 | 0.1746 | +0.034 | +0.031 | +0.033 | 0.023 | 0.023 | 0.023 |
| onset | 6 | 0.1326 | 0.1319 | 0.1318 | +0.025 | +0.026 | +0.029 | 0.024 | 0.024 | 0.023 |

(all offsets :00–:50 and horizons in `reports/subhour_backtest.md`; skill = Brier skill vs
the 28-day climatology, ECE = expected calibration error, 10 bins). No offset is worse
than :00 by more than 0.0015 Brier, so the models are not retrained on mixed-phase origins.

**Why a keep-alive, and why it is bounded.** GitHub runs `schedule` best effort: in this
repository the hourly forecast cron (`7 * * * *`) fired twice between 08:40 and 22:30 UTC
on 11 Oct 2026, the 3-hourly Pages cron 48 minutes late, the 30-minute Telegram cron
twice. A 10-minute site cannot rely on it. The layers, from most to least reliable:

1. **External pinger** (recommended, needs the owner): cron-job.org calls the
   `workflow_dispatch` endpoint every 10 minutes with a fine-grained token that can only
   run Actions in this repository (step by step in `deploy/README.md`).
2. **Keep-alive** (on by default): after every run (also one the gate skipped as a
   duplicate) a small job waits until the next slot + 3 minutes, checking every 20 s,
   and dispatches the workflow only if nothing (cron, pinger, a person) has started a
   newer run by then; as soon as a newer run appears it exits. It is a dead man's
   switch, not a loop: runs never overlap (one concurrency group), a chain of runs
   started only by the keep-alive stops after `PIPELINE_KEEPALIVE_MAX_HOPS` (default
   72, about 12 hours) until a real trigger restarts it, and `PIPELINE_KEEPALIVE=off`
   disables it. In the end-to-end test (PR #10) a run finished at 22:49, nothing else
   started one for the 22:53 slot, and the keep-alive dispatched the next run at
   22:56:02, which ran normally and continued the chain.
3. **`schedule`** every 10 minutes at :03/:13/… (off the congested top of the hour).

Terms: Actions minutes on standard runners are free and unlimited for public
repositories, jobs are capped at 6 hours (ours: 30 minutes, the keep-alive 20), at most
one pipeline run executes at a time, and the work is the project's own build, forecast
and publication, which is what Actions is for. The cost of the keep-alive is honest to
state: while it is on, one runner is reserved between runs, mostly sleeping (about
8 of every 10 minutes). With the external pinger configured it is not needed and should
be switched off; that is the recommended setup.

**Robustness.** The poll, threat-type and verification steps are `continue-on-error`
(the forecast then uses the last stored poll; the site keeps the previous layer). The
store commit runs even when the forecast failed, so no observation is lost; pushes
rebase over the daily history job. When the model cache is empty or stale (evicted
after 7 days unused, modelling code changed: the key contains its hash) the pipeline
trains once without the holdout, saves the bundle and asks the train job for a full
one. A gate skips triggers that land within 5 minutes of the last update. The site
shows "updated HH:MM" and a warning when its data is more than 30 minutes old.

**Optional layers (hooks).** Steps marked `[optional]` / `[hook: ...]` in the workflows run
only when the code they need exists, so the pipeline works before and after those layers
land:

* *Raion layer* (`src/airraid/raions.py` …): the official raion-level file is cached
  under a fixed key (it stopped updating on 2026-09-07) and downloaded once; the store
  checkout widens to 31 days (the full raion model needs 28 days of history); the raion
  models are trained by the train job (`scripts/train_models.py --raions`) into
  `<models>/raions`, which is cached and restored with the oblast bundle, and the model
  cache key also hashes the raion code. Interface expected from
  `airraid_app.raion_engine.RaionForecaster`: `save(dir)` / `load_saved(dir)` next to
  `prepare()`; the static build loads them from `AIRRAID_RAION_MODEL_DIR` (default
  `<AIRRAID_MODEL_DIR>/raions`), and `AIRRAID_RAION_TRAIN=0` (set by the pipeline) forbids
  training inside the 10-minute run.
* *Text signals* (`src/airraid/text/collect.py`): `.github/scripts/text-data.sh` updates
  the `text-data` archive before the forecast and commits it after the run; the
  threat-type step reads its posts from there when `predict_type_now.py --store` exists.

**What the owner must configure**

1. Settings → Pages → Source: **GitHub Actions** (the `github-pages` environment accepts
   `main` only; that is where the pipeline deploys from).
2. Secret `ALERTS_IN_UA_TOKEN` (already set). Without it the site shows the forecast as of
   the end of the volunteer dataset.
3. Optional: the external pinger (`deploy/README.md`), then `PIPELINE_KEEPALIVE=off`.
4. Optional: variables `PIPELINE_KEEPALIVE` (`off`), `PIPELINE_KEEPALIVE_MAX_HOPS`.
5. Optional: secret `HF_TOKEN` + variable `HF_SPACE` for the FastAPI server on a Hugging
   Face Space (`deploy-space.yml`, creates the Space and copies the alerts token).

## What went wrong along the way

* **The first full run scored validation Brier 0.0000 and AUC 1.0.** The label column
  `y` sat in the same table as the features and was picked up as a feature. The leakage
  test did not catch it because it compared *features* between full and truncated data,
  and both contained the same label. Fix: label columns are excluded by construction,
  there is a regression test, and `run.py` now aborts if validation AUC > 0.995.
* **The first target was too easy.** "Alert active in the next hour" is mostly
  persistence: if an alert is running now, the next hour almost surely has one. After
  reviewing related work I added the `onset` target, which is what a forecast is
  actually useful for.
* **The "official" dataset looked authoritative but was a month stale** and changed
  its definition in December 2025, so it was dropped in favour of the volunteer file.
* **The live merge treated any alerts.in.ua record as a whole-oblast alert since its
  earliest start, and trained on it.** Month-long frontline hromada alerts made
  Kharkivska and Dnipropetrovska "under alert" for weeks; the hourly run forecast 0.999 /
  0.000. Fixed by the measured definition and the training/feature split above, with
  regression tests (`tests/test_live_alerts.py`) that fail when the old merge is put back.
* **The first evaluation run hit the sandbox's 30-minute job limit** and lost its
  results. Model selection is now cached (`--reuse-selection`) and logistic regression
  trains on the last 12 months only.

## Limitations

* The volunteer data is updated about once a day. Without the store or a token the
  "current state" features lag by up to a day; with them, the gap is filled from
  alerts.in.ua records, which only approximate the volunteer channel (see "Live data").
* The store starts on 2026-09-11. Scheduled runs (pipeline, train, history) start only
  once the workflows are on the default branch, and GitHub's schedule is best effort
  (see "Automation" for the keep-alive and the external pinger).
* No threat type: the alert data does not say *why* an alert was declared (drones,
  missiles, ballistic). Telegram monitoring channels could add this; see `text-features/`.
* Oblast level only. Since December 2025 real alerts are often per district, so an
  oblast "alert" here means "an alert somewhere in the oblast".
* Regime shifts (new attack tactics, seasons) are handled only by retraining.

## Repository layout

```
src/airraid/   data.py (load, clean, minute grid) · features.py · models.py · evaluate.py
               training.py (the served model set) · nowcast.py (origin rule, live forecast)
               verification.py · live_alerts.py (alerts.in.ua parsing, live definition)
               store.py · collect.py
src/airraid_app/  web app (FastAPI) and the engine behind the static site
run.py         evaluation pipeline → reports/
predict_now.py live forecast + forecast log
verify.py      forecast verification
scripts/pipeline.py         glue for the workflows: gate, timings, status, keep-alive, retrain trigger
scripts/train_models.py     the train job
scripts/build_static_site.py   the GitHub Pages site (store mode in the pipeline)
scripts/backtest_subhour.py    calibration at :00 vs :10 ... :50 origins
scripts/live_definition_check.py   live definition vs the training data
.github/workflows/   pipeline.yml (10 min) · train.yml · collect.yml (daily history) · ci.yml
                     deploy-space.yml (optional) · threat-labels.yml
tests/         leakage, live merge, store and verification tests
reports/       metrics tables, figures, selection.json
```

Related work reviewed: Ddrakkx/alerts-forecast (MIT), lbgos/AirAlerts,
arXiv 2411.14625. No code was copied.
