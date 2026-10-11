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
```

`predict_now.py` optionally reads an alerts.in.ua token from the `ALERTS_IN_UA_TOKEN`
environment variable to know which alerts are running *right now*. The token is never
stored in the repo.

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

## Learning loop (live)

* `predict_now.py` forecasts the next 6 hours for every region and appends each forecast
  to `reports/forecast_log.csv` (origin, region, target, horizon, model probability,
  climatology probability, model version).
* Models are retrained at most once every 24 h (`--retrain-hours`) on everything known
  at that moment, and are reused in between (`models/`, not committed). Training
  origins whose label window is not yet covered by the history file are dropped. In
  live mode the hours since the last history update would otherwise look like
  "no alerts" and teach the model false zeros.
* `verify.py` looks up what actually happened once a forecast's horizon has passed and
  writes `reports/verification.csv` and `reports/verification_summary.json`: Brier of
  the model vs climatology over the last 24 h / 7 d / all time, hit rate, recall and
  false alarm ratio at p ≥ 0.5. It uses the same column names as the web app's forecast
  journal, so it can score either one.
* `predict_now.py --as-of 2026-10-10T12:00` replays the live path at a past hour: data
  after that hour is dropped and models are retrained. I used it to test the loop end to
  end, forecast first and then `verify.py`. `tests/test_verify.py` checks that
  verification uses exactly the labels the models were trained on.

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
* **The first evaluation run hit the sandbox's 30-minute job limit** and lost its
  results. Model selection is now cached (`--reuse-selection`) and logistic regression
  trains on the last 12 months only.

## Limitations

* The volunteer data is updated about once a day, so without an alerts.in.ua token the
  "current state" features lag. With the token, current alerts are fresh but the
  history between the last update and now only contains the alerts still running.
* No threat type: the alert data does not say *why* an alert was declared (drones,
  missiles, ballistic). Telegram monitoring channels could add this; see `text-features/`.
* Oblast level only. Since December 2025 real alerts are often per district, so an
  oblast "alert" here means "an alert somewhere in the oblast".
* Regime shifts (new attack tactics, seasons) are handled only by retraining.

## Repository layout

```
src/airraid/   data.py (load, clean, minute grid) · features.py · models.py · evaluate.py
run.py         evaluation pipeline → reports/
predict_now.py live forecast + forecast log
verify.py      forecast verification
tests/         leakage and verification tests
reports/       metrics tables, figures, selection.json
```

Related work reviewed: Ddrakkx/alerts-forecast (MIT), lbgos/AirAlerts,
arXiv 2411.14625. No code was copied.
