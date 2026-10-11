Raion-level test on the latest weeks (labels: alerts.in.ua history in the live store), origins 2026-09-13 23:00:00+00:00 .. 2026-10-10 23:00:00+00:00 UTC, weekly refit, 119 units, 77,112 forecasts per target and horizon.

### target = active, brier

| model             |      1 |      2 |      3 |      6 |
|:------------------|-------:|-------:|-------:|-------:|
| climatology_avail | 0.1595 | 0.1596 | 0.1596 | 0.1597 |
| persistence       | 0.1265 | 0.2182 | 0.2362 | 0.2671 |
| oblast            | 0.2208 | 0.1943 | 0.1942 | 0.1946 |
| oblast_x_share    | 0.1983 | 0.182  | 0.182  | 0.185  |
| lightgbm_state    | 0.0847 | 0.1305 | 0.1394 | 0.1498 |

### target = active, bss_vs_clim

| model             |       1 |       2 |       3 |       6 |
|:------------------|--------:|--------:|--------:|--------:|
| climatology_avail |  0      |  0      |  0      |  0      |
| persistence       |  0.207  | -0.3673 | -0.4799 | -0.6727 |
| oblast            | -0.3845 | -0.2178 | -0.2169 | -0.2189 |
| oblast_x_share    | -0.2433 | -0.1403 | -0.1405 | -0.1585 |
| lightgbm_state    |  0.4691 |  0.182  |  0.1263 |  0.0615 |

### target = active, roc_auc

| model             |      1 |      2 |      3 |      6 |
|:------------------|-------:|-------:|-------:|-------:|
| climatology_avail | 0.847  | 0.847  | 0.8471 | 0.8473 |
| persistence       | 0.8384 | 0.7419 | 0.7231 | 0.691  |
| oblast            | 0.7547 | 0.7615 | 0.7622 | 0.7636 |
| oblast_x_share    | 0.8229 | 0.832  | 0.832  | 0.8325 |
| lightgbm_state    | 0.9416 | 0.8913 | 0.8791 | 0.862  |

### target = onset, brier

| model             |      1 |      2 |      3 |      6 |
|:------------------|-------:|-------:|-------:|-------:|
| climatology_avail | 0.1239 | 0.1676 | 0.1769 | 0.1655 |
| persistence       | 0.2449 | 0.2996 | 0.3017 | 0.2765 |
| oblast            | 0.1474 | 0.2071 | 0.2225 | 0.2179 |
| oblast_x_share    | 0.1229 | 0.1677 | 0.1772 | 0.1633 |
| lightgbm_state    | 0.1116 | 0.1565 | 0.1681 | 0.1613 |

### target = onset, bss_vs_clim

| model             |       1 |       2 |       3 |       6 |
|:------------------|--------:|--------:|--------:|--------:|
| climatology_avail |  0      |  0      |  0      |  0      |
| persistence       | -0.9773 | -0.7881 | -0.7052 | -0.6709 |
| oblast            | -0.1899 | -0.2362 | -0.2577 | -0.3166 |
| oblast_x_share    |  0.0075 | -0.0008 | -0.0015 |  0.0134 |
| lightgbm_state    |  0.0987 |  0.066  |  0.0499 |  0.0253 |

### target = onset, roc_auc

| model             |      1 |      2 |      3 |      6 |
|:------------------|-------:|-------:|-------:|-------:|
| climatology_avail | 0.7338 | 0.7771 | 0.8037 | 0.8428 |
| persistence       | 0.543  | 0.6294 | 0.6755 | 0.7225 |
| oblast            | 0.7041 | 0.7314 | 0.755  | 0.8    |
| oblast_x_share    | 0.7419 | 0.7767 | 0.8017 | 0.8442 |
| lightgbm_state    | 0.8038 | 0.8059 | 0.8188 | 0.8454 |

Base rates: active h=1: 0.390, active h=2: 0.391, active h=3: 0.391, active h=6: 0.392, onset h=1: 0.160, onset h=2: 0.283, onset h=3: 0.371, onset h=6: 0.522
