# Validation Evidence

This file covers the three validation deliverables from the Project Guide: the Power BI vs. app table, one independent check, and the $1,000 modified-data test.

## Power BI vs. app

The Power BI figures come from our report file, `Fleischnamnn_Mey_NP_EX_B_EPresso.pbix`. Its data model was read with `pbixray` and summed the same way the report's visuals do (implicit Sum of Revenue; the report has no DAX measures). The app figures come from the hosted app with the original files uploaded. Before submitting, confirm one or two of these against the visuals in Power BI Desktop and add a screenshot.

| Metric | Power BI result | App result | Match? / Explanation |
|---|---|---|---|
| Total revenue (five years) | $42,174,440 | $42,174,440 | Match. All 25,200 sales rows are identical row by row. |
| Website revenue | $27,218,880 | $27,218,880 | Match. Channel = StoreType "Website" (store SH00). |
| Brick-and-mortar revenue | $14,955,560 | $14,955,560 | Match. StoreType "Brick & Mortar" (stores SH01–SH20). |
| Revenue by year | 2025: $7,181,750; 2026: $8,016,950; 2027: $8,824,720; 2028: $8,969,470; 2029: $9,181,550 | Same five values | Match for every year. |
| Monthly revenue pattern | Jan 7.74%, Feb 3.83%, Mar 4.44%, Apr 5.15%, May 5.72%, Jun 6.50%, Jul 6.42%, Aug 6.45%, Sep 7.11%, Oct 8.31%, Nov 12.71%, Dec 25.61% | Same shares | Match. Both use each month's revenue (all years combined) divided by total revenue. |
| Additional measure: revenue by category | Espresso Machines $27,093,220; Beans $5,672,720; Frothers $4,329,350; Syrups $2,712,040; Accessories $2,367,110 | Same five values | Match. |

There were no discrepancies to explain, but we did find one difference in how the two models are built. In Power BI, Sales has no direct relationship to Stores: store attributes reach Sales through the survey table (Sales → Survey Results → E-Presso Stores). That gives the right totals only because every store has a survey row for every year. The app joins Sales to Stores directly on `Store ID`, which does not depend on survey coverage, and keeps the survey separate because it is one row per store per year. Details: [`powerbi_reconciliation.md`](powerbi_reconciliation.md).

## Independent check

`scripts/independent_check.py` recalculates the measures above with plain pandas, reading the exported CSVs and joining Sales to Stores on its own. It uses none of the app's code. Run it with `.venv/bin/python scripts/independent_check.py`. It printed a total of $42,174,440.00, the same channel split, the same five yearly totals, the same twelve monthly shares and the same category totals as the table above. It also asserts that the store join did not change the row count.

A second independent source agrees too: the Sales table read straight from `Support_EX_B_Sales.accdb` and the Sales table inside the Power BI model have the same 25,200 rows with no revenue differences.

## $1,000 modified-data test record

The predictions below were written down before the modified file was uploaded. The test ran in one app session: the original files were loaded, then the modified copy was uploaded in their place, then the originals were restored, with no code edits and no restart.

| Item | Entry |
|---|---|
| Table and row identifier | Sales, `Sales ID` S0020250101 |
| Date / store / channel of the row | 2025-01-31 / SH00 Website / Website; category Beans (C1), product French Roast (P01) |
| Original value | $6,780.00 |
| Modified value | $7,780.00 (`data/modified/Sales_plus1000.csv`; exactly one row differs from the original export) |
| Expected total revenue after change | $42,174,440 + $1,000 = $42,175,440 |
| Actual total revenue after change | $42,175,440.00 |
| Chart that should change, and what you saw | Revenue by year and channel: Website 2025 should rise from $3,160,230 to $3,161,230, and it did. Revenue by month: January should rise from $3,266,270 to $3,267,270, and it did. Revenue by category: Beans should rise from $5,672,720 to $5,673,720, and it did. Points that should not move (Brick & Mortar 2025 at $4,021,520, Website 2026 at $4,784,750, February at $1,616,410, the other four categories) did not move. |
| Filter combinations checked (changed / unchanged) | Changed: 2025 + Website went from $3,160,230 to $3,161,230; 2025 + Beans went from $984,990 to $985,990. Unchanged: 2025 + Brick & Mortar stayed $4,021,520; 2026 + Website stayed $4,784,750; store SH01 (all years) stayed $727,080. |
| Assistant, same question before and after | "What was total revenue in 2025?" answered $7,181,750 before and $7,182,750 after. "How much revenue did the website generate in 2025?" answered $3,160,230 before and $3,161,230 after (Agent Test 5). |
| Code changes required? | None. The app's cache is keyed on each file's contents, so the new upload was recalculated immediately. |
| Original data restored? | Yes. Re-uploading the original files brought the total back to $42,174,440.00. The original files were never edited; the change was made in a separate copy. |

Before the defense, repeat this once on the hosted app and take screenshots of the Total revenue KPI and the Revenue by year and channel chart before and after.
