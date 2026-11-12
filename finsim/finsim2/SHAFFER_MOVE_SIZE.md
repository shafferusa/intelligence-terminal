# How big will the move be? Shaffer Directional 1D – 1W move size — research (stage 3)

Built 2026-09-27 22:51:23 (protocol SHAFFER_MOVE_SIZE_PROTOCOL.md). P(up) stays the point-in-time prior-only model (stage 1 found no new information that beats it); this study forecasts the SIZE of the next day's and week's move.

QLIKE gain = M0's loss − the model's loss (positive = better), per date, walk-forward. PASSED needs G1 (gain t ≥ 2), G2 (gain in all but one complete era, ≥ 3 eras; split t ≥ 1), G3 (90% range covers 87–93% out of sample) and BH FDR q = 0.10.

| Horizon | Model | Records | QLIKE | QLIKE gain (t) | Eras won | Split t | 90% coverage | Status |
|---|---|---|---|---|---|---|---|---|
| 1D | M0 base (σ5/σ21/σ63, VIX, today's move) | 336295 | +1.5516 | — (—) | —/— | — | 90.4% | reference |
| 1D | M1 + event calendar | 336295 | +1.5399 | +0.0115 (+8.6) | 4/4 | +7.4 | 90.4% | PASSED |
| 1D | M2 + 8-K events | 336295 | +1.5512 | +0.0004 (+2.4) | 4/4 | -0.2 | 90.3% | NOT VALIDATED |
| 1D | M3 + calendar + 8-K | 336295 | +1.5394 | +0.0120 (+8.6) | 4/4 | +7.1 | 90.4% | PASSED |
| 1W | M0 base (σ5/σ21/σ63, VIX, today's move) | 134429 | +0.4938 | — (—) | —/— | — | 89.9% | reference |
| 1W | M1 + event calendar | 134429 | +0.4780 | +0.0156 (+7.4) | 4/4 | +5.5 | 90.1% | PASSED |
| 1W | M2 + 8-K events | 134429 | +0.4932 | +0.0005 (+3.5) | 4/4 | +3.5 | 89.9% | PASSED |
| 1W | M3 + calendar + 8-K | 134429 | +0.4776 | +0.0160 (+7.5) | 4/4 | +5.5 | 90.0% | PASSED |

## Today (2026-09-25) — largest expected moves (research display)

**1D** (model M3)

| Asset | Expected move (1σ) | 90% range | Macro release next session | Earnings window (5d) |
|---|---|---|---|---|
| INTC | ±4.6% | [-6.8%, +7.1%] | no | no |
| AMD | ±3.9% | [-5.8%, +6.0%] | no | no |
| SOL | ±3.5% | [-5.3%, +5.5%] | no | — |
| META | ±3.5% | [-5.3%, +5.4%] | no | no |
| SQQQ | ±3.4% | [-5.2%, +5.3%] | no | — |
| TQQQ | ±3.3% | [-5.0%, +5.1%] | no | — |
| QCOM | ±3.1% | [-4.7%, +4.8%] | no | no |
| BRENT | ±3.0% | [-4.5%, +4.6%] | no | — |
| ETH | ±3.0% | [-4.5%, +4.6%] | no | — |
| WTI | ±3.0% | [-4.5%, +4.6%] | no | — |
| NATGAS | ±3.0% | [-4.5%, +4.6%] | no | — |
| CRM | ±3.0% | [-4.5%, +4.6%] | no | no |
| USO | ±2.9% | [-4.5%, +4.6%] | no | — |
| COFFEE | ±2.9% | [-4.4%, +4.5%] | no | — |
| ORCL | ±2.8% | [-4.2%, +4.3%] | no | no |

**1W** (model M3)

| Asset | Expected move (1σ) | 90% range | Macro release next session | Earnings window (5d) |
|---|---|---|---|---|
| INTC | ±11.2% | [-15.5%, +18.1%] | no | no |
| AMD | ±8.8% | [-12.6%, +14.2%] | no | no |
| SOL | ±8.3% | [-11.9%, +13.4%] | no | — |
| META | ±8.2% | [-11.8%, +13.2%] | no | no |
| SQQQ | ±8.0% | [-11.6%, +12.9%] | no | — |
| TQQQ | ±7.7% | [-11.2%, +12.4%] | no | — |
| QCOM | ±7.2% | [-10.4%, +11.5%] | no | no |
| BRENT | ±7.1% | [-10.3%, +11.4%] | no | — |
| WTI | ±7.0% | [-10.3%, +11.3%] | no | — |
| CRM | ±7.0% | [-10.3%, +11.3%] | no | no |
| COFFEE | ±6.9% | [-10.1%, +11.1%] | no | — |
| ETH | ±6.9% | [-10.1%, +11.1%] | no | — |
| USO | ±6.9% | [-10.0%, +11.0%] | no | — |
| NATGAS | ±6.7% | [-9.8%, +10.8%] | no | — |
| TSLA | ±6.5% | [-9.5%, +10.3%] | no | no |

