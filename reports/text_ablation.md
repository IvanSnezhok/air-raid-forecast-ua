Telegram ablation. Test: walk-forward, weekly retrain, origins 2026-09-12 23:00 .. 2026-10-10 23:00 UTC (same weeks and rows as metrics_test.md).
`lightgbm_cov` (alert history only) and `lightgbm_text` (alert history + Telegram) are trained on the same rows: origins from 2025-01-03 00:00 UTC (text coverage) to the fold start minus the 6 h embargo. Trees chosen on the validation month for each model separately.
Channels: deraketaua, kpszsu, monitorwarr, war_monitor. Rules: strict, permissive (the first is the main text model).

### target = active, brier

| model                            |      1 |        2 |        3 |        6 |
|:---------------------------------|-------:|---------:|---------:|---------:|
| climatology_28d                  | 0.1835 |   0.1836 |   0.1834 |   0.1832 |
| lightgbm (all history, main run) | 0.1323 |   0.1768 |   0.1784 |   0.1787 |
| lightgbm_cov                     | 0.1321 |   0.1765 |   0.1778 |   0.179  |
| lightgbm_text                    | 0.1312 |   0.1763 |   0.1782 |   0.1792 |
| lightgbm_text_permissive         | 0.1307 | nan      | nan      | nan      |

### target = onset, brier

| model                            |      1 |        2 |      3 |        6 |
|:---------------------------------|-------:|---------:|-------:|---------:|
| climatology_28d                  | 0.1533 |   0.1833 | 0.1819 |   0.1368 |
| lightgbm (all history, main run) | 0.1462 |   0.1761 | 0.1756 |   0.132  |
| lightgbm_cov                     | 0.1461 |   0.1759 | 0.1747 |   0.1317 |
| lightgbm_text                    | 0.1449 |   0.174  | 0.173  |   0.1313 |
| lightgbm_text_permissive         | 0.1449 | nan      | 0.1723 | nan      |

### target = active, bss_vs_clim

| model                            |      1 |        2 |        3 |        6 |
|:---------------------------------|-------:|---------:|---------:|---------:|
| climatology_28d                  | 0      |   0      |   0      |   0      |
| lightgbm (all history, main run) | 0.2789 |   0.0366 |   0.0272 |   0.0246 |
| lightgbm_cov                     | 0.2801 |   0.0387 |   0.0306 |   0.0231 |
| lightgbm_text                    | 0.2851 |   0.0398 |   0.0281 |   0.0223 |
| lightgbm_text_permissive         | 0.2874 | nan      | nan      | nan      |

### target = onset, bss_vs_clim

| model                            |      1 |        2 |      3 |        6 |
|:---------------------------------|-------:|---------:|-------:|---------:|
| climatology_28d                  | 0      |   0      | 0      |   0      |
| lightgbm (all history, main run) | 0.0466 |   0.0394 | 0.0345 |   0.0348 |
| lightgbm_cov                     | 0.0473 |   0.0406 | 0.0394 |   0.0371 |
| lightgbm_text                    | 0.0552 |   0.0507 | 0.0487 |   0.0401 |
| lightgbm_text_permissive         | 0.0548 | nan      | 0.0529 | nan      |

### target = active, roc_auc

| model                            |      1 |        2 |        3 |        6 |
|:---------------------------------|-------:|---------:|---------:|---------:|
| climatology_28d                  | 0.7521 |   0.7522 |   0.7526 |   0.7532 |
| lightgbm (all history, main run) | 0.8562 |   0.7713 |   0.7658 |   0.7647 |
| lightgbm_cov                     | 0.8568 |   0.772  |   0.7678 |   0.7642 |
| lightgbm_text                    | 0.8616 |   0.7745 |   0.7672 |   0.7635 |
| lightgbm_text_permissive         | 0.8629 | nan      | nan      | nan      |

### target = onset, roc_auc

| model                            |      1 |        2 |      3 |        6 |
|:---------------------------------|-------:|---------:|-------:|---------:|
| climatology_28d                  | 0.7501 |   0.7815 | 0.8019 |   0.8576 |
| lightgbm (all history, main run) | 0.7792 |   0.7976 | 0.813  |   0.8648 |
| lightgbm_cov                     | 0.7793 |   0.7985 | 0.815  |   0.865  |
| lightgbm_text                    | 0.7875 |   0.8055 | 0.8208 |   0.8664 |
| lightgbm_text_permissive         | 0.7874 | nan      | 0.8222 | nan      |

### target = active, log_loss

| model                            |      1 |        2 |        3 |        6 |
|:---------------------------------|-------:|---------:|---------:|---------:|
| climatology_28d                  | 0.5483 |   0.5485 |   0.5479 |   0.5474 |
| lightgbm (all history, main run) | 0.4066 |   0.5214 |   0.5259 |   0.527  |
| lightgbm_cov                     | 0.4068 |   0.5205 |   0.5241 |   0.528  |
| lightgbm_text                    | 0.403  |   0.5188 |   0.5239 |   0.5271 |
| lightgbm_text_permissive         | 0.4019 | nan      | nan      | nan      |

### target = onset, log_loss

| model                            |      1 |        2 |      3 |        6 |
|:---------------------------------|-------:|---------:|-------:|---------:|
| climatology_28d                  | 0.4819 |   0.5472 | 0.5418 |   0.4314 |
| lightgbm (all history, main run) | 0.447  |   0.519  | 0.5172 |   0.4082 |
| lightgbm_cov                     | 0.4472 |   0.5189 | 0.5153 |   0.4086 |
| lightgbm_text                    | 0.4427 |   0.514  | 0.5117 |   0.4086 |
| lightgbm_text_permissive         | 0.443  | nan      | 0.5098 | nan      |

### target = active, calibration_error

| model                    |      1 |        2 |        3 |        6 |
|:-------------------------|-------:|---------:|---------:|---------:|
| climatology_28d          | 0.0013 |   0.0013 |   0.0013 |   0.0012 |
| lightgbm_cov             | 0.0002 |   0.0004 |   0.0003 |   0.0004 |
| lightgbm_text            | 0.0005 |   0.0006 |   0.0004 |   0.0003 |
| lightgbm_text_permissive | 0.0004 | nan      | nan      | nan      |

### target = onset, calibration_error

| model                    |      1 |        2 |      3 |        6 |
|:-------------------------|-------:|---------:|-------:|---------:|
| climatology_28d          | 0.0011 |   0.0014 | 0.002  |   0.003  |
| lightgbm_cov             | 0.0001 |   0.0005 | 0.0007 |   0.001  |
| lightgbm_text            | 0.0004 |   0.0007 | 0.0012 |   0.0008 |
| lightgbm_text_permissive | 0.0003 | nan      | 0.0011 | nan      |

### Brier difference vs lightgbm_cov (negative = text helps), 95 % CI by day-block bootstrap

| target   |   horizon_h | model                    |   brier_diff_vs_cov |   ci95_lo |   ci95_hi |   rel_change_pct |
|:---------|------------:|:-------------------------|--------------------:|----------:|----------:|-----------------:|
| active   |           1 | lightgbm_text            |            -0.00092 |  -0.00208 |   0.00027 |         -0.69432 |
| active   |           1 | lightgbm_text_permissive |            -0.00134 |  -0.00272 |   0.00015 |         -1.01403 |
| active   |           2 | lightgbm_text            |            -0.0002  |  -0.00138 |   0.001   |         -0.11055 |
| active   |           3 | lightgbm_text            |             0.00045 |  -0.00038 |   0.00125 |          0.25453 |
| active   |           6 | lightgbm_text            |             0.00015 |  -0.00086 |   0.0012  |          0.08514 |
| onset    |           1 | lightgbm_text            |            -0.00121 |  -0.00238 |   1e-05   |         -0.82762 |
| onset    |           1 | lightgbm_text_permissive |            -0.00114 |  -0.00257 |   0.00036 |         -0.78141 |
| onset    |           2 | lightgbm_text            |            -0.00184 |  -0.00375 |  -5e-05   |         -1.04848 |
| onset    |           3 | lightgbm_text            |            -0.00171 |  -0.00394 |   0.0006  |         -0.97635 |
| onset    |           3 | lightgbm_text_permissive |            -0.00246 |  -0.00464 |  -0.00031 |         -1.41007 |
| onset    |           6 | lightgbm_text            |            -0.00041 |  -0.00324 |   0.00252 |         -0.31204 |

### Top text features by gain (lightgbm_text)

All text features together: 16.2 % of the total gain. Top 15:

|                         |   gain_share |
|:------------------------|-------------:|
| tg_reg_min_since_threat |       0.0213 |
| tg_nat_regions_60m      |       0.0192 |
| tg_summary_age_h        |       0.0154 |
| tg_summary_drones       |       0.0154 |
| tg_min_since_national   |       0.0119 |
| tg_min_since_platform   |       0.0109 |
| tg_nat_drone_180m       |       0.0096 |
| tg_reg_min_since_any    |       0.0083 |
| tg_nat_drone_60m        |       0.0061 |
| tg_nat_platform_180m    |       0.006  |
| tg_nat_posts_60m        |       0.0057 |
| tg_reg_drone_180m       |       0.004  |
| tg_nat_missile_180m     |       0.0038 |
| tg_nat_kab_180m         |       0.0038 |
| tg_nat_launch_180m      |       0.0032 |

Selected trees: lightgbm_cov|active_h1=194, lightgbm_text|active_h1=351, lightgbm_cov|active_h2=151, lightgbm_text|active_h2=159, lightgbm_cov|active_h3=228, lightgbm_text|active_h3=346, lightgbm_cov|active_h6=263, lightgbm_text|active_h6=170, lightgbm_cov|onset_h1=189, lightgbm_text|onset_h1=409, lightgbm_cov|onset_h2=185, lightgbm_text|onset_h2=222, lightgbm_cov|onset_h3=183, lightgbm_text|onset_h3=268, lightgbm_cov|onset_h6=180, lightgbm_text|onset_h6=403
