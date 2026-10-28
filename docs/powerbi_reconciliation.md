# Power BI Reconciliation

Source of the Power BI figures is `Fleischnamnn_Mey_NP_EX_B_EPresso.pbix`. On 2026-10-28 its data model was read with `pbixray`, which pulls the tables Power BI stored after Power Query ran. Those figures were compared with the source files in `data/raw/`. The report has no DAX measures, so every visual uses Power BI's implicit "Sum of Revenue". Recomputing that sum from the model's own Sales table reproduces what the visuals show. Still, a manual check of one or two visuals in Power BI Desktop is worth doing before the defense.

## Result: exact match

The PBIX Sales table and the Access Sales table are identical row for row. Both have 25,200 rows, every `Sales ID` matches, and no revenue value differs.

| Measure | Power BI model | Source data | Difference |
|---|---|---|---|
| Total revenue | 42,174,440 | 42,174,440 | 0 |
| 2025 | 7,181,750 | 7,181,750 | 0 |
| 2026 | 8,016,950 | 8,016,950 | 0 |
| 2027 | 8,824,720 | 8,824,720 | 0 |
| 2028 | 8,969,470 | 8,969,470 | 0 |
| 2029 | 9,181,550 | 9,181,550 | 0 |

## Figures behind the other visuals

Revenue by StoreType (bar chart): Website 27,218,880 and Brick & Mortar 14,955,560. Drilling down to Market gives 5-Global 27,218,880, 4-Large 6,219,740, 3-Medium 3,642,250, 2-Minor 2,904,630 and 1-Small 2,188,940.

Each month's share of total revenue (column chart, all years combined): January 7.74%, February 3.83%, March 4.44%, April 5.15%, May 5.72%, June 6.50%, July 6.42%, August 6.45%, September 7.11%, October 8.31%, November 12.71% and December 25.61%.

Revenue by category (slicer): Espresso Machines 27,093,220, Beans 5,672,720, Frothers 4,329,350, Syrups 2,712,040 and Accessories 2,367,110.

The average satisfaction across all non-blank surveys is 3.401.

## How the Power BI model is wired

The model has four active relationships, all many-to-one with single-direction filtering. Sales joins to Categories on `Category ID`, to Products on `Product ID`, and to Survey Results on `StoreYear` → `Survey ID`. Survey Results then joins to E-Presso Stores on `Store ID` → `ID`. Sales has no direct relationship to Stores, so store attributes reach Sales through the survey table. That gives correct totals here only because every store-year has a survey row. The Streamlit app should join Sales to Stores directly on `Store ID`, which is simpler and does not depend on survey coverage.

The Power Query steps also differ slightly from the raw files. E-Presso Stores drops `Phone` and `Manager`. Survey Results converts `Satisfaction` to a number but leaves `Interest` as text. An unused duplicate query, `Support_EX_B_Stores`, is also loaded. None of these affect revenue.
