# Calculator guide — BA II Plus and TI-84 Plus (CFA Math, SPEC §0e)

The one reference every lesson's keystrokes must agree with. Logan owns a TI-84 Plus and will sit
the exam with a BA II Plus (the TI-84 is **not** allowed in the exam room: CFA Institute permits only
the TI BA II Plus / BA II Plus Professional and the HP 12C family, and voids results earned with
anything else). Every walkthrough therefore shows both, BA II Plus first.

Notation on the page: each key is a `kbd` element spelled exactly as printed on the key. A second
function is written as the `2ND` key followed by the label printed above the key (`2ND` `CLR TVM`),
never as the key underneath. TI-84 menu choices are `kbd class="menu"` with their number
(`1:Finance`). Typed numbers are written as one `kbd` (`1000`). Displays are `samp`.

## Shared conventions (both calculators)

- **Sign rule.** Cash you pay out is negative, cash you receive is positive. Solving for FV, PV or
  PMT never errors: the answer simply comes back with the opposite sign. Solving for the rate (I/Y,
  I%) or N needs at least one sign change among PV, PMT and FV; if they all share a sign the BA II
  Plus shows `Error 5` and the TI-84 Plus shows `ERR:NO SIGN CHNG`.
- **Rates are typed in percent.** 5% is typed `5`, not `0.05`, in I/Y (BA) and I% (TI-84), in
  `npv(` and in the ICONV/►Eff tools.
- **Displayed precision.** Lessons show results to **4 decimals** (both calculators set to 4 in
  the one-time setup). Write BA II Plus displays with thousands separators as it shows them
  (`FV = 1,628.8946`) and TI-84 displays without (`FV=1628.8946`).
- **Annual problems use P/Y = C/Y = 1.** For other frequencies, lessons adjust N and the rate per
  period themselves (N = years × m, rate = annual rate / m) and keep P/Y = C/Y = 1, which is what
  CFA prep teaches and what avoids the factory-setting trap. Say so when it comes up.

## One-time setup

| | BA II Plus | TI-84 Plus |
|---|---|---|
| Payments per year = 1 | `2ND` `P/Y` `1` `ENTER` (C/Y follows), `2ND` `QUIT` | in the TVM Solver: `P/Y=1`, `C/Y=1` |
| 4 decimals | `2ND` `FORMAT` `4` `ENTER` `2ND` `QUIT` | `MODE`, row `FLOAT 0123456789`, pick `4`, `ENTER`, `2nd` `MODE` (QUIT) |
| Order of operations | `2ND` `FORMAT`, `↓` to `Chn`/`AOS`, `2ND` `SET` until `AOS`, `2ND` `QUIT` (factory setting is Chn) | always algebraic |
| Reset everything | `2ND` `RESET` `ENTER` (then redo the three lines above: reset puts P/Y back to 12 — 1 on the BA II Plus Professional — decimals to 2 and the method to Chn) | — |
| Statistics screens | — | turn STAT WIZARDS off so the commands below paste directly: `MODE`, row `STAT WIZARDS`, choose `OFF`, `ENTER` (TI-84 Plus OS 2.55MP or later and every CE; older software has no wizards) |

## Time value of money (TVM)

**BA II Plus.** `2ND` `CLR TVM` first. Type each known value then its key: `N`, `I/Y`, `PV`, `PMT`,
`FV`. Negative numbers: type the number then `+|−`. Solve: `CPT` then the unknown's key.
Annuity due: `2ND` `BGN` `2ND` `SET` `2ND` `QUIT` (the display shows `BGN`); switch back the same
way (shows nothing = END). Clearing TVM does **not** reset BGN/END or P/Y.

**TI-84 Plus.** `APPS` `1:Finance` `1:TVM Solver` (choosing a menu item takes one press: its
number, or `ENTER` on the highlighted item — never both). Fill `N=`, `I%=`, `PV=`,
`PMT=`, `FV=`, `P/Y=`, `C/Y=`, and highlight `END` or `BEGIN` on the `PMT:` row and press `ENTER`.
Negative numbers use the `(−)` key (not the subtraction key). Solve: move the cursor to the unknown
and press `ALPHA` `ENTER` (SOLVE); a small square marks the solved line. Old values stay until
overwritten, so set every line (type `0` where a value is absent).
Quick functions from the same Finance menu: `tvm_Pmt`, `tvm_I%`, `tvm_PV`, `tvm_N`, `tvm_FV` use
the values already in the solver.

## Uneven cash flows: NPV and IRR

**BA II Plus.** `CF` `2ND` `CLR WORK`. `CFo=` (the display spells it with a letter o): type the
value (negative for an outlay) `ENTER`,
`↓`. `C01=`: value `ENTER` `↓`; `F01=`: how many times it repeats `ENTER` (default 1) `↓`; then
`C02=` … NPV: `NPV`, `I=` rate `ENTER`, `↓`, `CPT` (shows `NPV=`). IRR: `IRR` `CPT`.

**TI-84 Plus.** From `APPS` `1:Finance`: `7:npv(` and `8:irr(`.
`npv(rate, CF0, {CF1, CF2, …}[, {freq1, freq2, …}])` — rate in percent, braces are `2nd` `(` and
`2nd` `)`. `irr(CF0, {CF1, CF2, …}[, {freqs}])` returns a percent.
Example: `npv(10,−1000,{300,400,500})` and `irr(−1000,{300,400,500})`.

## Interest conversion (nominal ↔ effective)

**BA II Plus.** `2ND` `ICONV`: `NOM=` (annual nominal %) `ENTER` `↓` `EFF=` `↓` `C/Y=` (periods
per year) `ENTER`, `↑` back to `EFF=` and `CPT`. Reverse: enter `EFF`, `C/Y`, go to `NOM`, `CPT`.

**TI-84 Plus.** Finance menu `C:►Eff(` → `►Eff(nominal %, periods per year)`;
`B:►Nom(` → `►Nom(effective %, periods per year)`.

## Amortization

**BA II Plus.** Set up and solve the loan in TVM first (PMT computed). `2ND` `AMORT`: `P1=` first
payment number `ENTER` `↓` `P2=` last payment number `ENTER` `↓` then `BAL=`, `↓` `PRN=`, `↓`
`INT=` are shown for payments P1 to P2.

**TI-84 Plus.** With the loan solved in the TVM Solver: Finance menu `9:bal(npmt)` (balance after
payment npmt), `0:ΣPrn(p1,p2)` and `A:ΣInt(p1,p2)` (principal and interest paid in payments p1–p2).
These read the solver's values. The BA II Plus rounds amortization results to the displayed
decimals (4 here), and the TI-84 functions can round too (they take an optional rounding argument,
e.g. `bal(12,9)`), so the two calculators can differ in the last digit or two: give amortization
questions a tolerance that covers it (about 0.05 on a six-figure balance).

## Statistics on a list of numbers

**BA II Plus.** Enter data: `2ND` `DATA` `2ND` `CLR WORK`, then `X01=` value `ENTER` `↓` `Y01=`
(for one-variable data this is the frequency, leave 1) `↓` `X02=` … Results: `2ND` `STAT`, press
`2ND` `SET` until `1-V` shows (one variable) or `LIN` (two-variable, regression), then `↓` through
the results. `1-V`: `n`, `x̄`, `Sx` (sample standard deviation), `σx` (population), `Σx`, `Σx²`.
`LIN`: `n`, `x̄`, `Sx`, `σx`, `ȳ`, `Sy`, `σy`, `a` (intercept), `b` (slope), `r` (correlation),
`X'`, `Y'` (predictions: type a value at one and `CPT` the other), `Σx`, `Σx²`, `Σy`, `Σy²`, `Σxy`.
In `1-V` the Y entries are frequencies and must be whole numbers. For a probability-weighted mean or
standard deviation either work by hand, or enter whole-number frequencies proportional to the
probabilities (probabilities 0.45/0.15/0.10/0.30 → Y = 45/15/10/30) and read `x̄` and `σx` (the
population figure is the right one for a probability distribution). Sample covariance =
`r` × `Sx` × `Sy`.

**Using a worksheet result in arithmetic (BA II Plus).** While the value is on screen press `STO`
and a digit (`STO` `1`); leave the worksheet with `2ND` `QUIT`; then use it with `RCL` `1`. Storing
avoids retyping rounded displays, which can move later answers in the last decimals.

**TI-84 Plus.** `STAT` `1:Edit…`, type values in `L1` (and `L2`). `STAT`, `►` to `CALC`,
`1:1-Var Stats` (then `L1` — `2nd` `1` — `ENTER`): shows `x̄`, `Σx`, `Σx²`, `Sx`, `σx`, `n`,
min, Q1, Med, Q3, max. Two variables: `2:2-Var Stats L1,L2`. Regression: `8:LinReg(a+bx) L1,L2`
(shows a, b, and r, r² once diagnostics are on: `2nd` `0` (CATALOG) `DiagnosticOn` `ENTER` `ENTER`
works on every version; TI-84 Plus OS 2.55MP or later and every CE also have `MODE`
`STAT DIAGNOSTICS: ON`). With STAT WIZARDS left on, these commands open a fill-in screen (List:,
FreqList:, Calculate) instead of pasting — same result, different keys; lessons assume wizards off.

## Distributions (TI-84 only — the BA II Plus has none)

`2nd` `VARS` (DISTR): `2:normalcdf(lower, upper[, μ, σ])`, `3:invNorm(area[, μ, σ])`,
`4:invT(area, df)`, `6:tcdf(lower, upper, df)`, `8:χ²cdf(lower, upper, df)`, `0:Fcdf(lower, upper,
df1, df2)`, `A:binompdf(n, p, k)`, `B:binomcdf(n, p, k)`. Use `−1E99` / `1E99` for −∞ / +∞ (`EE` is
`2nd` `,`). Hypothesis tests: `STAT` `►` `►` `TESTS` (`2:T-Test…`, `4:2-SampTTest…`,
`C:χ²-Test…`, `E:2-SampFTest…`, `F:LinRegTTest…`).
These menu numbers are for TI-84 Plus OS 2.30 or later and every CE (`invT(` arrived in 2.30). On
older software there is no `invT(` and the DISTR items after it move up one (`5:tcdf(`,
`7:χ²cdf(`, `9:Fcdf(`, `0:binompdf(`, `A:binomcdf(`) and TESTS reads `D:2-SampFTest…`,
`E:LinRegTTest…`. Day 1 asks Logan to check his version: `2nd` `+` (MEM) `1:About`. When a lesson
names a menu item, name the function too (e.g. "`6:tcdf(`") so an older menu still works.
On the exam (BA II Plus) these come from a table in the question or from memorized critical values
(z: 1.645, 1.96, 2.33, 2.58 — say "2.326" and "2.576" when precision matters). Lessons say so
whenever they use a TI-84 distribution function.

## Arithmetic you will use constantly

| Operation | BA II Plus | TI-84 Plus |
|---|---|---|
| power xʸ | `1.05` `yˣ` `10` `=` | `1.05` `^` `10` `ENTER` |
| root (x^(1/n)) | `2` `yˣ` `(` `1` `÷` `3` `)` `=` (or `2` `yˣ` `3` `1/x` `=`) | `2` `^` `(` `1` `÷` `3` `)` |
| eˣ | value `2ND` `eˣ` | `2nd` `LN` (eˣ) value `)` |
| ln | value `LN` | `LN` value `)` |
| change sign | `+|−` after the number | `(−)` before the number |
| square / root | `x²`, `√x` after the number | `x²` after; `2nd` `x²` (√) before |
| reciprocal | `1/x` after the number | `x⁻¹` after |
| factorial, combinations | `n` `2ND` `x!`; `n` `2ND` `nCr` `r` `=` | `MATH`, `►` to `PRB` (`PROB` on a CE): `4:!`, `3:nCr` |
| store / recall | `STO` `1`, `RCL` `1` | value `STO→` `ALPHA` `A`, then `ALPHA` `A` |
| last answer | `2ND` `ANS` | `2nd` `(−)` (ANS) |

## Other BA II Plus worksheets the course uses

- `2ND` `BOND` (bond price, yield, accrued interest): `SDT=` settlement date as MM.DDYY `ENTER`
  `↓` `CPN=` annual coupon % `ENTER` `↓` `RDT=` maturity date `ENTER` `↓` `RV=` 100 `↓` day count
  (`2ND` `SET` toggles ACT / 360) `↓` coupons per year (`2ND` `SET` toggles 2/Y / 1/Y) `↓` `YLD=`
  `ENTER` `↓` `PRI` `CPT` `↓` `AI`. Lessons price most bonds with TVM instead, which works on both
  calculators; the BOND worksheet appears only where accrued interest is the point.
- `2ND` `Δ%` (above the `5` key; percent change): `OLD=`, `NEW=`, `%CH=`, `#PD=` (periods) —
  computes a compound growth rate when `#PD` is set.
- `2ND` `DEPR` (depreciation), `2ND` `DATE` (days between dates), `2ND` `BRKEVN` (breakeven).
