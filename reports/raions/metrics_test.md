Raion-level test (walk-forward, weekly refit): origins 2026-08-09 19:00:00+00:00 .. 2026-09-06 19:00:00+00:00 UTC, 119 units (118 raions + Kyiv City).

### target = active, brier

| model                  |      1 |      2 |      3 |      6 |
|:-----------------------|-------:|-------:|-------:|-------:|
| climatology_28d        | 0.1399 | 0.1399 | 0.14   | 0.1399 |
| rate_28d               | 0.1359 | 0.1361 | 0.1364 | 0.1365 |
| persistence            | 0.084  | 0.152  | 0.1697 | 0.1948 |
| oblast                 | 0.2036 | 0.175  | 0.1741 | 0.1711 |
| oblast_x_share         | 0.1687 | 0.1539 | 0.1529 | 0.153  |
| oblast_x_share_stale30 | 0.1714 | 0.1559 | 0.1548 | 0.1545 |
| lightgbm_state         | 0.0602 | 0.0972 | 0.1011 | 0.112  |
| lightgbm               | 0.0574 | 0.0914 | 0.0972 | 0.1109 |

### target = active, bss_vs_clim

| model                  |       1 |       2 |       3 |       6 |
|:-----------------------|--------:|--------:|--------:|--------:|
| climatology_28d        |  0      |  0      |  0      |  0      |
| rate_28d               |  0.0288 |  0.027  |  0.0259 |  0.0239 |
| persistence            |  0.3993 | -0.0861 | -0.2122 | -0.3925 |
| oblast                 | -0.4551 | -0.2504 | -0.2436 | -0.2232 |
| oblast_x_share         | -0.2061 | -0.1    | -0.0922 | -0.0938 |
| oblast_x_share_stale30 | -0.2255 | -0.1143 | -0.1057 | -0.105  |
| lightgbm_state         |  0.5698 |  0.305  |  0.2775 |  0.199  |
| lightgbm               |  0.5899 |  0.347  |  0.3058 |  0.2068 |

### target = active, roc_auc

| model                  |      1 |      2 |      3 |      6 |
|:-----------------------|-------:|-------:|-------:|-------:|
| climatology_28d        | 0.8517 | 0.8516 | 0.8514 | 0.8512 |
| rate_28d               | 0.8597 | 0.859  | 0.8585 | 0.8578 |
| persistence            | 0.849  | 0.7639 | 0.7417 | 0.71   |
| oblast                 | 0.7527 | 0.7505 | 0.752  | 0.7532 |
| oblast_x_share         | 0.8151 | 0.8178 | 0.8199 | 0.8197 |
| oblast_x_share_stale30 | 0.81   | 0.8117 | 0.814  | 0.8141 |
| lightgbm_state         | 0.9609 | 0.9228 | 0.9161 | 0.8991 |
| lightgbm               | 0.9651 | 0.9319 | 0.9232 | 0.9004 |

### target = onset, brier

| model                  |      1 |      2 |      3 |      6 |
|:-----------------------|-------:|-------:|-------:|-------:|
| climatology_28d        | 0.0873 | 0.1315 | 0.1523 | 0.1658 |
| rate_28d               | 0.086  | 0.1306 | 0.1511 | 0.1636 |
| persistence            | 0.1691 | 0.2267 | 0.2368 | 0.2253 |
| oblast                 | 0.1192 | 0.1945 | 0.2283 | 0.2592 |
| oblast_x_share         | 0.0883 | 0.1343 | 0.1542 | 0.1676 |
| oblast_x_share_stale30 | 0.0898 | 0.1376 | 0.1586 | 0.1734 |
| lightgbm_state         | 0.0759 | 0.1142 | 0.1323 | 0.1392 |
| lightgbm               | 0.0737 | 0.109  | 0.1244 | 0.1312 |

### target = onset, bss_vs_clim

| model                  |       1 |       2 |       3 |       6 |
|:-----------------------|--------:|--------:|--------:|--------:|
| climatology_28d        |  0      |  0      |  0      |  0      |
| rate_28d               |  0.0157 |  0.0073 |  0.008  |  0.0132 |
| persistence            | -0.936  | -0.7236 | -0.5552 | -0.3586 |
| oblast                 | -0.3648 | -0.4788 | -0.4991 | -0.563  |
| oblast_x_share         | -0.0109 | -0.0213 | -0.0126 | -0.0107 |
| oblast_x_share_stale30 | -0.0285 | -0.0465 | -0.0416 | -0.0455 |
| lightgbm_state         |  0.1312 |  0.1316 |  0.1313 |  0.1604 |
| lightgbm               |  0.1567 |  0.1712 |  0.183  |  0.2085 |

### target = onset, roc_auc

| model                  |      1 |      2 |      3 |      6 |
|:-----------------------|-------:|-------:|-------:|-------:|
| climatology_28d        | 0.7569 | 0.7856 | 0.8006 | 0.828  |
| rate_28d               | 0.762  | 0.7839 | 0.8001 | 0.8312 |
| persistence            | 0.545  | 0.6289 | 0.6864 | 0.7585 |
| oblast                 | 0.7149 | 0.7363 | 0.7544 | 0.7798 |
| oblast_x_share         | 0.748  | 0.774  | 0.7944 | 0.8239 |
| oblast_x_share_stale30 | 0.7374 | 0.7626 | 0.7839 | 0.8158 |
| lightgbm_state         | 0.8543 | 0.8502 | 0.8535 | 0.8721 |
| lightgbm               | 0.8647 | 0.8647 | 0.8709 | 0.8885 |

Base rates: active h=1: 0.277, active h=2: 0.277, active h=3: 0.276, active h=6: 0.275, onset h=1: 0.104, onset h=2: 0.189, onset h=3: 0.254, onset h=6: 0.371

Chosen on validation (by data available at forecast time):

| target | history | live_state | none |
|---|---|---|---|
| active_h1 | lightgbm | lightgbm_state | oblast_x_share_stale30 |
| onset_h1 | lightgbm | lightgbm_state | oblast_x_share_stale30 |
| active_h2 | lightgbm | lightgbm_state | oblast_x_share_stale30 |
| onset_h2 | lightgbm | lightgbm_state | oblast_x_share_stale30 |
| active_h3 | lightgbm | lightgbm_state | oblast_x_share_stale30 |
| onset_h3 | lightgbm | lightgbm_state | oblast_x_share_stale30 |
| active_h6 | lightgbm | lightgbm_state | oblast_x_share_stale30 |
| onset_h6 | lightgbm | lightgbm_state | oblast_x_share_stale30 |
