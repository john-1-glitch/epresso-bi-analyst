# E-Presso Data Profile and Grain Statements (Step 1)

Profiled 2026-10-28 from the three files used for the Power BI assignment, copied unmodified into `data/raw/`. The Access database cannot be uploaded to Streamlit, so its tables were exported to `data/exported/` with `scripts/export_access.py`. The export round-trips exactly: 25,200 rows and 42,174,440.00 in revenue, both before and after.

## Grain table

| Table | Source | One row = | Key column(s) | Row count | Contains revenue? |
|---|---|---|---|---|---|
| Sales | `Support_EX_B_Sales.accdb` → `Sales` | one product at one store in one month | `Sales ID` (unique); also unique on `Store ID` + `Date` + `Product ID` | 25,200 | Yes (`Revenue`) |
| Stores | `Support_EX_B_Stores.csv` (tab-separated) | one store, including the website | `ID` | 21 | No |
| Categories | `Support_EX_B_Sales.accdb` → `Categories` | one product category | `Category ID` | 5 | No |
| Products | `Support_EX_B_Sales.accdb` → `Products` | one product | `Product ID` | 20 | No |
| Survey Results | `Support_EX_B_Survey.xlsx`, sheet `Survey Results` (header on row 3) | one store's survey in one year | `Survey ID` (= `Store ID` + `_YY`) | 105 | No |

The row count confirms the Sales grain: 21 stores × 20 products × 60 months = 25,200. Every month from January 2025 to December 2029 appears exactly once per store and product, with 5,040 rows per year.

## Relationships

All joins run many-to-one from Sales to a lookup table with a unique key, so none of them should duplicate or drop revenue. Every Sales key has a match. There are no orphan store, category, product or store-year values.

| From (many) | To (one) | Key |
|---|---|---|
| Sales.`Store ID` | Stores.`ID` | store |
| Sales.`Category ID` | Categories.`Category ID` | category |
| Sales.`Product ID` | Products.`Product ID` | product |
| Sales.`StoreYear` | Survey.`Survey ID` | store + year |

`StoreYear` always equals `Store ID` + `_` + the two-digit year of `Date`. Each product belongs to exactly one category.

The survey join needs care in Step 2. Survey is store-by-year, but Sales is store-by-product-by-month. Merging Survey onto Sales repeats each satisfaction score 240 times (20 products × 12 months), so satisfaction must be averaged at the store-year grain, never across merged sales rows. The revenue total stays correct either way, because the join is many-to-one.

## Revenue baseline (to reconcile with Power BI in Step 2)

Total revenue is 42,174,440.00. By year: 2025 is 7,181,750; 2026 is 8,016,950; 2027 is 8,824,720; 2028 is 8,969,470; and 2029 is 9,181,550. The website (SH00) accounts for 27,218,880, or 64.5% of the total. Revenue ranges from 20 to 546,350 per row, with no zero, negative or blank values.

## Data quality notes

- Sales has no blank values and no duplicate rows.
- The website (SH00) has blank City, State and PostalCode in Stores. That is expected, since it has no physical location. Its StoreType is "Website" and its Market is "5-Global".
- The website's five survey rows (SH00_25 to SH00_29) have blank Satisfaction and Interest. The website was not surveyed, so it must be excluded from satisfaction averages, not counted as zero.
- Satisfaction is on a 0–5 scale, and the observed range is 0.4–5.0. The low values (SH12 in 2028 at 0.4, SH12 and SH06 in 2027 at 0.7) look like genuine poor scores, not entry errors, but they are worth flagging. Interest is on a 0–100 scale, ranging from 62 to 99.
- Survey `Year` is stored as text ("Y2025"), so it has to be parsed to match sales years.
- The February 2028 sales date is the 28th, not the 29th (a leap year). Every other date is a month-end. This doesn't affect monthly or yearly totals.
- Three products in different categories share the name "Base Model" (P11, P15, P19). Any product-level reporting should use `Product ID` or show the category alongside the name.
- Store SH11's phone number is missing its opening parenthesis. This is cosmetic and not used in any analysis.
- The Stores CSV is tab-separated despite its `.csv` extension. The starter loader detects this automatically.

## Field mapping for the assignment's parameters

`year` comes from `Sales.Date`. `store` is `Stores.ID`, which can be labeled with City and Location. `channel` maps to `Stores.StoreType`: "Website" for online sales and "Brick & Mortar" for the 20 physical stores. The data has no separate channel column, so that mapping is an assumption to confirm with the professor. Category is available for the dashboard slicer. There is no cost, margin or customer data, so profit and per-customer questions cannot be answered.
