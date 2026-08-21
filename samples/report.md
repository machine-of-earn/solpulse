# Solana ecosystem report

*Generated 2026-08-21T11:32:40Z by solpulse 0.1.0 (`solpulse/snapshot/v1`).*

## At a glance

- **Network health:** OK — RPC node reports a healthy, caught-up cluster.
- **Epoch 1,020** is 8.58% complete (~40h 11m remaining).
- **Throughput:** 4,207 TPS total, 2,352 TPS excluding vote transactions, at a measured 366.4 ms slot time.
- **Validators:** 684 active, 10 delinquent (0.070% of stake).
- **Market:** SOL $90.95 (▲ +4.22% 24h); DeFi TVL $5.47B (▲ +13.16% 7d).

## Anomalies

| Severity | Metric | Deviation | What was seen |
|---|---|---|---|
| · INFO | `superminority_count` | — | Only 18 validators hold the superminority — that many colluding or failing together could halt finality. |

### How this was judged

- **Baseline:** 2 prior record(s) in the history log, 2026-08-21T09:04:23Z → 2026-08-21T11:32:40Z.
- **Deviation rules did not run:** the baseline needs at least 5 records. Absolute threshold rules (node health, delinquent stake, slot time, failed sources) still applied — those need no history. No baseline was invented from too few points.

## Network performance

| Metric | Value |
|---|---|
| Health | ok |
| Epoch | 1,020 |
| Epoch progress | 8.58% |
| Slots remaining | 394,936 |
| Epoch ETA | 40h 11m |
| Absolute slot | 440,677,064 |
| Block height | 418,726,723 |
| Transaction count | 540,307,467,850 |
| TPS (mean, all) | 4,207.02 |
| TPS (mean, non-vote) | 2,351.86 |
| TPS (latest sample) | 4,166.92 |
| Slot time (measured) | 366.4 ms |
| Slot time (target) | 400.0 ms |
| Sample window | 30m |

## Validators

| Metric | Value |
|---|---|
| Active validators | 684 |
| Delinquent validators | 10 |
| Delinquent share (count) | 1.44% |
| Delinquent share (stake) | 0.070% |
| Delinquent stake | 304,580 SOL |
| Superminority size | 18 |
| Top-10 stake share | 24.34% |
| Median commission | 5% |

### Top 10 validators by active stake

| # | Vote account | Stake (SOL) | Share | Commission |
|---|---|---|---|---|
| 1 | `CcaHc2…BzoTN1` | 17,066,372 | 3.940% | 7% |
| 2 | `he1ius…PauBtk` | 16,054,078 | 3.706% | 0% |
| 3 | `3N7s9z…eWiD5g` | 12,175,413 | 2.811% | 0% |
| 4 | `CatzoS…gZDiqb` | 11,782,032 | 2.720% | 5% |
| 5 | `26pV97…c53dJx` | 9,178,661 | 2.119% | 7% |
| 6 | `51JBzS…zgUNAm` | 8,917,577 | 2.059% | 10% |
| 7 | `8GbwAS…GJF8iD` | 8,402,660 | 1.940% | 0% |
| 8 | `9QU2QS…aM29mF` | 7,964,352 | 1.839% | 7% |
| 9 | `CvSb7w…aKwycB` | 7,357,821 | 1.699% | 5% |
| 10 | `DumiCK…sTZk4a` | 6,547,243 | 1.511% | 0% |

> Stake concentration is the number to watch here: the *superminority* is the smallest set of validators controlling more than one third of active stake, the threshold at which a colluding group could halt consensus.

## Economics

| Metric | Value |
|---|---|
| SOL price | $90.95 |
| SOL 24h change | ▲ +4.22% |
| Market cap | $53.03B |
| DeFi TVL | $5.47B |
| TVL 1d change | ▲ +4.74% |
| TVL 7d change | ▲ +13.16% |
| TVL 30d change | ▲ +9.43% |
| Circulating supply | 583,178,382 SOL |
| Non-circulating supply | 49,461,973 SOL |
| Total supply | 632,640,355 SOL |
| Circulating share | 92.18% |
| Inflation (total) | 3.685% |
| Inflation (validator) | 3.685% |

## Data sources

| Source | Status | Latency | Detail |
|---|---|---|---|
| `economics` | ok | 6,440.0 ms | collected |
| `network` | ok | 424.9 ms | collected |
| `price` | ok | 160.5 ms | collected |
| `tvl` | ok | 42.1 ms | collected |
| `validators` | ok | 962.6 ms | collected |

**5 of 5 sources returned data.**

---

Collected directly from the Solana JSON-RPC API plus keyless public endpoints (CoinGecko, DefiLlama). No API keys, no accounts, no dependencies beyond the Python standard library.
