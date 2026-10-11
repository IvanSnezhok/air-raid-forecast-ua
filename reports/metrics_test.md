Test period (walk-forward, weekly retrain): 2026-09-12 23:00 .. 2026-10-10 23:00 UTC (forecast origins), 24 regions.

### target = active, brier

| model               |      1 |      2 |      3 |      6 |
|:--------------------|-------:|-------:|-------:|-------:|
| climatology_28d     | 0.1835 | 0.1836 | 0.1834 | 0.1832 |
| persistence         | 0.2122 | 0.3266 | 0.3372 | 0.3391 |
| markov_persistence  | 0.161  | 0.2045 | 0.2141 | 0.1996 |
| logistic_regression | 0.1348 | 0.1803 | 0.1806 | 0.1806 |
| lightgbm_no_spatial | 0.1323 | 0.176  | 0.178  | 0.1788 |
| lightgbm            | 0.1323 | 0.1768 | 0.1784 | 0.1787 |

### target = active, bss_vs_clim

| model               |       1 |       2 |       3 |       6 |
|:--------------------|--------:|--------:|--------:|--------:|
| climatology_28d     |  0      |  0      |  0      |  0      |
| persistence         | -0.1565 | -0.7793 | -0.8389 | -0.8506 |
| markov_persistence  |  0.1225 | -0.1142 | -0.1672 | -0.0892 |
| logistic_regression |  0.2653 |  0.0176 |  0.015  |  0.0146 |
| lightgbm_no_spatial |  0.279  |  0.041  |  0.0295 |  0.0242 |
| lightgbm            |  0.2789 |  0.0366 |  0.0272 |  0.0246 |

### target = active, roc_auc

| model               |      1 |      2 |      3 |      6 |
|:--------------------|-------:|-------:|-------:|-------:|
| climatology_28d     | 0.7521 | 0.7522 | 0.7526 | 0.7532 |
| persistence         | 0.6805 | 0.5513 | 0.5393 | 0.5374 |
| markov_persistence  | 0.8207 | 0.7257 | 0.724  | 0.7231 |
| logistic_regression | 0.8508 | 0.7645 | 0.7627 | 0.7617 |
| lightgbm_no_spatial | 0.8571 | 0.7745 | 0.7676 | 0.7648 |
| lightgbm            | 0.8562 | 0.7713 | 0.7658 | 0.7647 |

### target = active, log_loss

| model               |      1 |      2 |      3 |      6 |
|:--------------------|-------:|-------:|-------:|-------:|
| climatology_28d     | 0.5483 | 0.5485 | 0.5479 | 0.5474 |
| persistence         | 1.4695 | 2.2613 | 2.3349 | 2.3478 |
| markov_persistence  | 0.506  | 0.6009 | 0.625  | 0.5862 |
| logistic_regression | 0.4181 | 0.5329 | 0.5331 | 0.5336 |
| lightgbm_no_spatial | 0.4087 | 0.5202 | 0.5252 | 0.5275 |
| lightgbm            | 0.4066 | 0.5214 | 0.5259 | 0.527  |

### target = onset, brier

| model               |      1 |      2 |      3 |      6 |
|:--------------------|-------:|-------:|-------:|-------:|
| climatology_28d     | 0.1533 | 0.1833 | 0.1819 | 0.1368 |
| persistence         | 0.3109 | 0.3631 | 0.353  | 0.2545 |
| markov_persistence  | 0.1784 | 0.24   | 0.2562 | 0.2301 |
| logistic_regression | 0.1488 | 0.1804 | 0.1798 | 0.1357 |
| lightgbm_no_spatial | 0.1464 | 0.1761 | 0.1759 | 0.1345 |
| lightgbm            | 0.1462 | 0.1761 | 0.1756 | 0.132  |

### target = onset, bss_vs_clim

| model               |       1 |       2 |       3 |       6 |
|:--------------------|--------:|--------:|--------:|--------:|
| climatology_28d     |  0      |  0      |  0      |  0      |
| persistence         | -1.0281 | -0.9807 | -0.9404 | -0.8599 |
| markov_persistence  | -0.1634 | -0.3094 | -0.4082 | -0.6815 |
| logistic_regression |  0.0297 |  0.0158 |  0.0113 |  0.0083 |
| lightgbm_no_spatial |  0.0452 |  0.0394 |  0.0328 |  0.0166 |
| lightgbm            |  0.0466 |  0.0394 |  0.0345 |  0.0348 |

### target = onset, roc_auc

| model               |      1 |      2 |      3 |      6 |
|:--------------------|-------:|-------:|-------:|-------:|
| climatology_28d     | 0.7501 | 0.7815 | 0.8019 | 0.8576 |
| persistence         | 0.566  | 0.6178 | 0.6463 | 0.7109 |
| markov_persistence  | 0.7197 | 0.732  | 0.7522 | 0.8144 |
| logistic_regression | 0.7696 | 0.7899 | 0.8077 | 0.86   |
| lightgbm_no_spatial | 0.7794 | 0.7995 | 0.8142 | 0.8634 |
| lightgbm            | 0.7792 | 0.7976 | 0.813  | 0.8648 |

### target = onset, log_loss

| model               |      1 |      2 |      3 |      6 |
|:--------------------|-------:|-------:|-------:|-------:|
| climatology_28d     | 0.4819 | 0.5472 | 0.5418 | 0.4314 |
| persistence         | 2.1529 | 2.5135 | 2.4437 | 1.762  |
| markov_persistence  | 0.5567 | 0.6926 | 0.7222 | 0.652  |
| logistic_regression | 0.4573 | 0.5326 | 0.5297 | 0.4192 |
| lightgbm_no_spatial | 0.4489 | 0.5202 | 0.5189 | 0.415  |
| lightgbm            | 0.447  | 0.519  | 0.5172 | 0.4082 |

Base rates: active h=1: 0.331, active h=2: 0.331, active h=3: 0.331, active h=6: 0.332, onset h=1: 0.234, onset h=2: 0.391, onset h=3: 0.496, onset h=6: 0.673
