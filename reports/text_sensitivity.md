# Telegram ablation: sensitivity runs

Same test weeks, rows and folds as `text_ablation.md`. Generated from `reports/text_ablation*.csv`, `text_selection*.json` and the cached fold predictions of `run.py --text-only`.

## 1. The edit rule

* **Main rule** (`strict`): a post that was already edited when we first saw it counts only from that sighting. In the backfilled history that is 2026-10-11, after the whole test period, so such posts are not used: 12,465 of 130,989 posts (9.5 %; 29 % of `deraketaua`, 2.7 % of `kpszsu`).
* **Excluding every post ever seen edited** (`unedited_only`) gives exactly the same features over the training and test period: no post was seen unedited and edited later before the test end (0 posts), and no post edited when first seen became available before it (0 posts). So that run equals the main one by construction and was not repeated.
* **Permissive bound** (`permissive`, NOT leak-free): every post at its publish time with its final text. Live collection sees most edited posts before the edit, so live features lie between `strict` and `permissive`. Run on three target/horizons (trees as for the main text model):

| target   |   h | Δ Brier strict − cov   | Δ Brier permissive − cov   |
|:---------|----:|:-----------------------|:---------------------------|
| active   |   1 | -0.0009 (-0.7 %)       | -0.0013 (-1.0 %)           |
| onset    |   1 | -0.0012 (-0.8 %)       | -0.0011 (-0.8 %)           |
| onset    |   3 | -0.0017 (-1.0 %)       | -0.0025 (-1.4 %)           |

Using the edited posts would add at most 0.3-0.4 percentage points of Brier improvement: the edit rule costs little, and the skew between training (edited posts dropped) and live (seen before the edit) is small.

## 2. Which channels carry the signal

Validation month (2026-08-15 .. 09-12), Brier of the model with early-stopped trees:

| target   |   h |   history only |   + kpszsu |   + all 4 channels |
|:---------|----:|---------------:|-----------:|-------------------:|
| active   |   1 |         0.1209 |     0.1172 |             0.1171 |
| active   |   2 |         0.1646 |     0.1632 |             0.1634 |
| active   |   3 |         0.1665 |     0.1658 |             0.1655 |
| active   |   6 |         0.1676 |     0.1676 |             0.1678 |
| onset    |   1 |         0.1361 |     0.1328 |             0.1327 |
| onset    |   2 |         0.164  |     0.1599 |             0.1593 |
| onset    |   3 |         0.1601 |     0.1558 |             0.1558 |
| onset    |   6 |         0.1229 |     0.1188 |             0.1185 |

Test weeks, where the kpszsu-only run finished (it was stopped to free CPU for the main run):

| target   |   h |   history only |   + kpszsu |   + all 4 channels |
|:---------|----:|---------------:|-----------:|-------------------:|
| active   |   1 |         0.1321 |     0.1314 |             0.1312 |
| active   |   2 |         0.1765 |     0.1762 |             0.1763 |
| active   |   3 |         0.1778 |     0.1778 |             0.1782 |

Almost all of the gain comes from the Air Force channel; the three volunteer radar channels add at most 0.0006 Brier on validation and nothing measurable on the test weeks.

## 3. When do the posts come?

Air Force (`kpszsu`) threat posts naming an oblast, per alert onset in that oblast
(2025-03-01 .. 2026-10-10, 52,673 onsets), by 15-minute bin relative to the onset:

| minutes from onset | −180..−120 | −120..−60 | −60..−30 | −30..−15 | −15..0 | 0..+15 | +15..+60 |
|---|---|---|---|---|---|---|---|
| posts per onset per 15 min | 0.10 | 0.11 | 0.12 | 0.13 | **0.21** | 0.15 | 0.08 |

The rate is flat at the background level until about an hour before the onset, rises by
15-25 % in the last hour, and doubles only in the 15 minutes right before the alert. Most of
what the channel says about an oblast is said when the alert is declared, which is why the
gain is limited to the next 1-3 hours and why the history model was already close.
