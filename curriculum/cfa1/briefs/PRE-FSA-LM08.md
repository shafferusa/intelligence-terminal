# PRE-FSA-LM08 — Non-Current (Long-Term) Liabilities
CFA Program Curriculum 2027, Prerequisite Reading, Volume 2: Financial Statement Analysis, Learning Module 8, printed pp.241–266 (PDF 249–274), from a full verified reading.

## Reading map
| Section | Printed | PDF | Priority | Why |
|---|---|---|---|---|
| Introduction | p.241 | 249 | SKIM | Orientation; bonds payable only. |
| Bonds Payable & Accounting for Bond Issuance | pp.242–245 | 250–253 | SKIM (opening terminology part) | Terminology only. |
| Accounting for Bond Issuance | pp.242–245 | 250–253 | CLOSE | PV pricing, par/discount/premium, issuance costs. |
| Accounting for Bond Amortisation, Interest Expense, and Interest Payments | pp.246–249 | 254–257 | CLOSE | Calculation core; cash-flow presentation. |
| Accounting for Bonds at Fair Value | pp.250–253 | 258–261 | STANDARD | Conceptual; fair value option. |
| Derecognition of Debt | pp.254–255 | 262–263 | STANDARD (only place LO 3 is taught) | Gain/loss rule; runs on to p.256 (PDF 264). |
| Summary | pp.256–257 | 264–265 | SKIM | Repeats earlier sections. |
| Practice Problems | pp.258–261 | 266–269 | CLOSE | Calculation drill. |
| Solutions | pp.262–266 | 270–274 | STANDARD | Read for the missed questions. |

## Learning outcomes
1. Determine how bonds are first recognised and measured, and how they are measured afterwards (amortised cost vs fair value). Issuance pp.242–245; amortisation pp.246–249; fair value pp.250–253.
2. Describe the effective interest method and compute interest expense, discount/premium amortisation and interest payments. pp.246–249.
3. Explain derecognition of debt (maturity or early redemption) and the resulting gain or loss. pp.254–256.

## Core principles
- **Price is the PV of the promises.** The issuer gets cash now and promises coupons plus face value. Investors pay the PV of those payments at the market rate for that risk. Market rate equal to coupon gives par, market above coupon gives a discount, below gives a premium. The coupon is fixed in the contract, so only the price can adjust. Assumes a flat yield curve (pp.242–245).
- **The issue-date market rate becomes the effective rate.** It stays fixed for a fixed-rate bond even if market rates move. The liability starts at proceeds, not face; cash received is a financing inflow (pp.243, 246).
- **Cash interest and expense differ unless the bond is at par.** Cash paid is coupon rate x face and never changes. Expense is the opening carrying amount x the effective rate. A discount means expense exceeds the coupon, the gap is non-cash, and the carrying amount climbs to face. A premium works in reverse (pp.246–249).
- **Effective-interest amortisation changes every year; straight-line is constant.** IFRS mandates the effective-interest method; US GAAP also favours it. Because the opening balance moves, amortisation grows over time (pp.247–250).
- **Cash-flow presentation.** Amortisation is non-cash, so the indirect operating reconciliation adds back discount amortisation and subtracts premium amortisation. Interest paid is operating under US GAAP, and operating or financing under IFRS. Principal repaid is financing (pp.246, 250).
- **Amortised cost versus fair value.** Most issuers use amortised cost, with fair value disclosed in the notes. If rates fall, fixed-rate debt is worth more, so leverage ratios look too low; if rates rise, the reverse. A fair value election is rare. A fall in the liability's value is a gain, but the part from the issuer's own credit risk goes to OCI (pp.250–253).
- **Early redemption.** The liability is removed at its carrying amount, and gain or loss = carrying amount minus cash paid. The gain or loss is stripped from operating cash flow, and the cash paid is a financing outflow. Such a gain is probably not recurring (pp.254–256).

## Vocabulary
- Non-current liability — obligation due beyond about a year (p.241).
- Indenture — the bond contract (p.242).
- Face value / par / maturity value — the principal repaid at maturity (p.242).
- Coupon rate — the stated rate applied to face; sets the cash interest (p.242).
- Market rate — yield investors require at issue; the discount rate (p.242).
- Effective interest rate — the single rate that discounts all promised payments to the issue price; the basis for expense (p.243).
- Discount / premium — issue price below / above face (pp.242–243).
- Zero-coupon bond — pays only principal, so sells at a discount when rates are positive (p.245).
- Issuance costs — costs of issuing; treatment differs by standard (p.245).
- Amortised cost — proceeds adjusted by cumulative amortisation (p.246).
- Carrying amount — the balance-sheet figure (also book value) (p.246).
- Straight-line method — even spread of discount or premium (p.247).
- Fair value option / FVTPL — electing to measure a liability at fair value (p.251).
- Own credit risk — the part of a fair value change due to the issuer's credit standing; goes to OCI (p.251).
- Extinguishment — early retirement of debt (p.254).
- Call price — redemption price stated as a percentage of face (p.254).

## Formulas
The book prints no numbered formulas; these working relationships are taken from the text and tables.
1. **Bond price** = sum over t=1..N of C/(1+r)^t + F/(1+r)^N (pp.243–245, 262). F face, C = coupon rate x F per period, r market rate per period, N periods; for semi-annual coupons halve the annual rate and double the periods. Assumes a flat yield curve. Intuition: price is the PV of the promised cash flows.
2. **Interest expense_t** = opening carrying amount x effective rate (p.247).
3. **Interest payment** = face x coupon rate (pp.242, 247).
4. **Amortisation_t** = |expense − payment|. Discount: adds to carrying amount. Premium: reduces it (pp.247–249).
5. **Ending carrying amount** = opening ± amortisation (discount +, premium −); equals face at maturity (pp.248–249).
6. **Straight-line amortisation per year** = |face − proceeds| / years; expense = coupon plus discount amortisation, or coupon minus premium amortisation (pp.248, 250).
7. **Gain/loss on redemption** = carrying amount − cash paid (positive = gain); cash paid = face x call price % (p.254). Under US GAAP any unamortised issuance cost is written off into this gain or loss.
8. **Price as % of face** = proceeds / face x 100 (p.244).
Limitation: expense uses the issue-date rate even if market rates later change.

## Traps
- Using the coupon rate for expense. Expense is opening carrying amount x issue-date effective rate; the coupon only sets cash paid (pp.243, 247).
- Getting the direction wrong: a discount carrying amount rises, a premium falls, both converge to face (p.246).
- Believing later market-rate moves change the effective rate on fixed-rate debt. They change fair value only (p.263 / PDF 271).
- Calculating the redemption loss from carrying amount instead of call price % x face. The loss is carrying amount minus that cash cost (p.254).
- Treating the repurchase gain as operating cash. It is removed in the indirect reconciliation, and the cash paid is financing (pp.254–256).
- Confusing IFRS and US GAAP: interest paid operating-only under US GAAP; issuance costs inside the IFRS carrying amount (pp.245, 250, 254).
- Using the annual rate and annual periods for a semi-annual bond, or forgetting that the straight-line expense is constant while effective-interest amortisation grows (p.244).
- Reading fair value as the reported liability. Falling rates raise the value of fixed debt, so book debt understates the burden (p.250).

## Original practice seeds
All hypothetical.
1. A £100,000 three-year bond pays a 4% annual coupon and is issued when the market rate is 5%. Price = 4,000/1.05 + 4,000/1.1025 + 104,000/1.157625 = about £97,276.75 (discount about £2,723). Year 1 expense = 97,276.75 x 5% = £4,863.84; cash coupon £4,000; amortisation £863.84; end-year-1 carrying amount £98,140.59. The £97,276.75 is a financing inflow.
2. Same bond, but the market rate is 6%. Price is about £94,654 (a larger discount, about £5,346). Year 1 expense = 94,654 x 6% = about £5,679, versus a coupon of £4,000, so about £1,679 is non-cash amortisation. Expense rises despite the lower starting balance, because the rate effect dominates.
3. A £100,000 zero-coupon bond due in 3 years is issued at a 5% market rate. Price = 100,000/1.157625 = about £86,384. Year 1 expense = about £4,319, all non-cash; carrying amount after year 1 about £90,703. If the company then buys it back for £88,000, gain = 90,703 − 88,000 = about £2,703 (income statement, removed from operating cash flow); the £88,000 is a financing outflow.

## End-of-module practice
17 multiple-choice questions, printed pp.258–261 (PDF 266–269). Solutions printed pp.262–266 (PDF 270–274), with PV tables and calculator keystrokes.
- Q1 — financing classification of bond proceeds
- Q2 — coupon versus market rate and discount
- Q3 — issuance cost treatment under US GAAP
- Q4-5 — PV pricing of premium and discount bonds
- Q6 — effective rate after market rates change
- Q7 — price and first-year expense
- Q8-10 — first-year expense and carrying amount (Q10: re-pricing remaining payments)
- Q11 — multi-year amortisation schedule
- Q12 — premium amortisation behaviour over time
- Q13 — straight-line method expense
- Q14 — zero-coupon bond effect on leverage
- Q15 — fair value versus carrying amount, rate moves
- Q16 — redemption gain or loss
- Q17 — redemption loss using call price

Suggested 8: Q2, Q4, Q7, Q10, Q12, Q14, Q15, Q17. Why: five on pricing and amortisation (LO1, LO2), Q15 on fair value, Q17 on LO3, Q14 for leverage.

## Book slips and uncertainties
- The book is inconsistent on US GAAP issuance costs: p.245 treats them as a deduction from the debt, p.254 as separately amortised, and the Q3 key fits neither cleanly. For concepts use p.245 (unamortised balance hits the redemption gain/loss); for Q3 follow the key.
- One worked example has a year typo on p.249 (PDF 257) and a totals row that omits expense totals.
- PDF pages 249, 260 and 264–274 were read as text only; solution tables were checked by recomputation.

## Workload
Difficulty 3/5. About 55 minutes of reading and 40 of practice, roughly 95 minutes. If you split it, do issuance and amortisation with Q1–13 first, then fair value, derecognition and Q14–17.

## Connections
Builds on time value of money, financial statement structure, the indirect cash flow method and IFRS/US GAAP basics. Later reuse: leases, pensions, deferred taxes, leverage ratios, Fixed Income bond pricing and call provisions.
