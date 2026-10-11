# Air raid alert forecasting for Ukraine

Hourly, per-region probabilistic forecasts of air raid alerts for the next 1–6 hours,
built from the full alert history since February 2022 and evaluated on the **latest
four weeks** (12 Sep – 11 Oct 2026) with a strict, leak-free walk-forward protocol.

Two questions are answered for each of 24 regions (23 oblasts + Kyiv City) at every
hour T:

| target | question | why |
|---|---|---|
| `active` | Will an alert be active at any moment of hour *T+h*? | what a person planning the next hours cares about |
| `onset`  | Will a **new** alert start within the next *h* hours? | harder and more useful: the "active" target is inflated by alerts that are already running |

## Quick start

```bash
pip install -r requirements.txt
python -m pytest            # leakage + verification tests (about 15 s)
python run.py               # full evaluation: model selection + 4-week walk-forward (about 30 min on 4 cores)
python run.py --reuse-selection   # skip model selection, reuse reports/selection.json
python predict_now.py       # live forecast for the next 6 hours (+ appends to the forecast log)
python verify.py            # score logged forecasts once their horizon has passed

# with the point-in-time store (a checkout of the `live-data` branch, see "Live data")
git worktree add live-data origin/live-data
python predict_now.py --store live-data   # fills the hours after the dataset, logs to the store
python verify.py --store live-data        # verifies within hours instead of a day later
python -m airraid.collect poll --root live-data      # what the collect workflow runs (needs the token)
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
orphan branch `live-data` (`.github/workflows/collect.yml`: a poll every 10 min, the
alerts.in.ua history once a day; the API only keeps ~30 days, so it was backfilled on
2026-10-11 from 2026-09-11):

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
```

`store.load(root, kind, as_of=T)` returns only rows known at T (`observed_at` /
`fetched_at` / `issued_at` ≤ T); for `alerts_history` it resolves each record (uid,
start, type) to the version known at T, with `first_seen_at` and `known_at` (when its end
became known). Files are never rewritten, so git grows only by new rows.

### What is and is not leak-free

* **Training never sees live data.** Models are fit on the volunteer file only, on origins
  whose label window it fully covers (`predict_now.grids`). Before this fix the training
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
  to `reports/forecast_log.csv` and, with `--store`, to the store's `forecasts`.
* **Retraining is a periodic refit**, not online learning: at most once every 24 h
  (`--retrain-hours`) the models are refit from scratch on the volunteer file as known at
  that moment, and reused in between (`models/`, not committed).
* `verify.py` looks up what actually happened once a forecast's horizon has passed and
  writes `reports/verification.csv` and `reports/verification_summary.json`: Brier of
  the model vs climatology over the last 24 h / 7 d / all time, hit rate, recall and
  false alarm ratio at p ≥ 0.5. With `--store` it reads the forecasts from the store,
  labels windows after the dataset end from the store's alerts.in.ua records (so within
  hours instead of a day later), and appends the result to the store. **Verification is
  monitoring:** it does not feed back into model weights. `tests/test_verify.py` checks
  that it uses exactly the training labels.
* `predict_now.py --as-of 2026-10-10T12:00` replays the live path at a past hour: data
  after that hour is dropped, store rows are used as they were known then, and models are
  retrained.

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
* The store starts on 2026-09-11; scheduled collection runs only once collect.yml is on
  the default branch.
* No threat type: the alert data does not say *why* an alert was declared (drones,
  missiles, ballistic). Telegram monitoring channels could add this; see `text-features/`.
* Oblast level only. Since December 2025 real alerts are often per district, so an
  oblast "alert" here means "an alert somewhere in the oblast".
* Regime shifts (new attack tactics, seasons) are handled only by retraining.

## Repository layout

```
src/airraid/   data.py (load, clean, minute grid) · features.py · models.py · evaluate.py
               live_alerts.py (alerts.in.ua parsing, live definition) · store.py · collect.py
run.py         evaluation pipeline → reports/
predict_now.py live forecast + forecast log
verify.py      forecast verification
scripts/live_definition_check.py   live definition vs the training data
tests/         leakage, live merge, store and verification tests
reports/       metrics tables, figures, selection.json
```

Related work reviewed: Ddrakkx/alerts-forecast (MIT), lbgos/AirAlerts,
arXiv 2411.14625. No code was copied.
