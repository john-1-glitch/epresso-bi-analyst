# E-Presso BI Analyst

## What this application does

E-Presso BI Analyst lets a manager upload the company's sales, store and survey files and see where revenue comes from: by year, by channel (website or physical stores), by product category and by store. It recalculates everything from the uploaded files, so a corrected or updated file changes every number on screen without touching the code. A built-in assistant answers plain-English questions such as "Is the online shop growing?" by running one of a small set of approved calculations and showing exactly what it ran.

## Live app

**[epreapp-bi-analyst-zx7d5bv7vmsnm3q3prkxfs.streamlit.app](https://epreapp-bi-analyst-zx7d5bv7vmsnm3q3prkxfs.streamlit.app/)**

The app may take a moment to wake up if nobody has used it recently. It starts empty: upload the files listed below to begin.

## How to use it

Upload the original course files in the sidebar. The quickest way is three files: the Access database fills Sales, Categories and Products at once.

| Upload box | File | Notes |
|---|---|---|
| Sales | `Support_EX_B_Sales.accdb` | The app reads the Sales, Categories and Products tables from the database |
| Stores | `Support_EX_B_Stores.csv` | Tab-separated; the app detects the delimiter |
| Survey Results (optional) | `Support_EX_B_Survey.xlsx` | The app finds the "Survey Results" sheet and its header row on its own |

CSV or Excel exports also work, one per table (for example `Sales.csv`, `Categories.csv` and `Products.csv` made with `scripts/export_access.py`). In a workbook or database, the app uses the sheet or table that has the required columns. It names any missing column instead of crashing, and an updated file with added or removed rows, blanks, or new stores, years or categories is recalculated without code changes.

The app has four tabs. **Data and quality** shows the grain of each table, a data quality summary and a profile of any table. **Revenue and merge checks** shows total revenue, revenue by year and the result of every join. **Dashboard** has Year, Channel, Store and Category filters (all built from the loaded data), three KPIs and three charts, each with a table view. **Assistant** answers questions, up to 15 per session.

## Data model

Every lookup table joins to Sales many-to-one on a unique key, so no join can duplicate or drop revenue.

| Table | One row = | Key | Rows | Contains revenue? |
|---|---|---|---|---|
| Sales | one product at one store in one month | `Sales ID` | 25,200 | Yes |
| Stores | one store (the website counts as store SH00) | `ID` | 21 | No |
| Categories | one product category | `Category ID` | 5 | No |
| Products | one product | `Product ID` | 20 | No |
| Survey Results | one store's survey in one year | `Survey ID` | 105 | No |

The Sales row count confirms its grain: 21 stores × 20 products × 60 months = 25,200 rows. Sales joins to Stores on `Store ID`, to Categories on `Category ID` and to Products on `Product ID`. Each join runs through `check_merge()`, which compares rows and revenue before and after and counts unmatched keys. On the real data, all three joins keep 25,200 rows and $42,174,440, with no unmatched keys.

Survey Results is deliberately not joined to Sales. It is one row per store per year, so joining it would repeat each satisfaction score on 240 sales rows. Full profile: [`docs/data_profile.md`](docs/data_profile.md).

## Metric definitions

**Revenue** is the sum of the `Revenue` column in the Sales table for the rows in scope. Rows with blank or non-numeric revenue are left out of the sum and counted in the Data Quality section; no row is ever deleted. **Share** is a group's revenue divided by the revenue of everything in the current scope. **Channel** is the store's `StoreType`: "Website" for online sales and "Brick & Mortar" for the 20 physical stores.

Revenue is not profit. The data has no costs, discounts or margins, so nothing in this app describes profitability.

## Assistant

We built **Option A, a live language-model agent** using Google Gemini with function calling, with the rules-based assistant (Option B) as its automatic fallback. Every answer says which one answered.

- **Gemini mode (when `GEMINI_API_KEY` is set):** Gemini reads the question and chooses one approved function and its arguments through function calling in `VALIDATED` mode, which constrains it to the listed functions and their schemas. Our code runs the function on the loaded data. Gemini then writes Findings, Interpretation and Next steps from the tool result. Before the app shows that text, it checks every number in it against the tool result (allowing for rounding, millions and percentages); if any number cannot be traced, the app shows the standard answer instead and says why. Gemini never sees the data itself.
- **Backup model:** the free tier allows about 20 requests per model per day and the main model (`gemini-3.5-flash`) is sometimes overloaded. On a 429 or 503 error the app retries once on `gemini-3.5-flash-lite`.
- **Rules-based mode (no key, or Gemini still fails):** keyword rules choose the function and a template writes the answer from the result. The answer shows a note explaining the switch.

| Tool | What it calculates | Arguments |
|---|---|---|
| `total_revenue` | Revenue in scope and its share of all revenue | year, channel, store |
| `revenue_by_year` | Revenue for each year and the change from first to last year | channel, store |
| `revenue_by_category` | Revenue and share for each product category | year, channel, store |
| `revenue_by_channel` | Revenue and share for Website vs Brick & Mortar | year, store |
| `top_stores` | The n stores with the most revenue, with shares | year, channel, n |
| `revenue_change_by_store` | Each store's change between two years, the largest % decline and growth, or a plain statement that no store declined | start_year, end_year, channel |
| `revenue_change_by_channel` | Each channel's change between two years and which grew the most | start_year, end_year, store |
| `satisfaction_vs_revenue` | Correlation between store-year revenue and survey satisfaction, excluding store-years without a score | year |
| `data_quality_report` | Rows, duplicates and missing values | none |

Every tool checks its arguments against the loaded data and returns a clear error for an unknown year, channel or store, or for a city that matches two stores; it never guesses. Every result follows the same contract: `tool`, `value`, `definition`, `scope`, `rows_used` and `caveats`. Each answer starts with its scope and says it uses the full loaded dataset; if dashboard filters are set, the Assistant tab says they do not apply. Then come Findings, Interpretation, Next steps and an expandable tool-call log with the function, arguments, raw result and which model answered. Sessions are limited to 15 questions.

**Limits it enforces:** profit, margin, forecast and customer-level questions are refused before any model is called, with the reason and a list of what it can answer. "Why" and "does X cause Y" questions get the relevant evidence plus a statement that the data shows association, not cause. "Should we..." questions get the evidence, "No recommendation is supported by this data", and the information a decision would need (expenses, profitability, leases, staffing, customer behavior). Scope, caveats and these limits are written by our code, not by the model.

**What it is not allowed to do:** run or write code, use `eval()` or `exec()`, write SQL or pandas, change the loaded data, or state a number our functions did not calculate.

## Validation

Full evidence: [`docs/validation.md`](docs/validation.md) (Power BI table, independent check, $1,000 test record) and [`docs/agent_tests.md`](docs/agent_tests.md) (five agent tests, two documented fixes with retests).

**Power BI comparison.** All five required measures plus revenue by category match our Power BI report exactly: total $42,174,440, Website $27,218,880, Brick & Mortar $14,955,560, every year, and every month's share (December is 25.61%). All 25,200 sales rows are identical row by row. One difference in design is documented: Power BI links Sales to Stores through the survey table, while the app joins Sales to Stores directly. Details: [`docs/powerbi_reconciliation.md`](docs/powerbi_reconciliation.md).

**Independent check.** `scripts/independent_check.py` recalculates every measure with plain pandas and none of the app's code. It gives the same results.

**$1,000 test.** Adding $1,000 to Sales row S0020250101 and uploading the copy in the same session raised the total to exactly $42,175,440. The Website 2025 chart point, the January bar, the Beans bar and the 2025 + Website filter each rose by $1,000, while Brick & Mortar, 2026 and store SH01 did not change. The assistant's answer moved by $1,000. There were no code changes and no restart, because the cache is keyed on each file's contents.

**Automated tests.** 83 tests in `tests/` run on a six-row dataset whose answers are worked out by hand in the test file. They cover totals, merge checks (including a duplicated store key and an unknown store), missing columns, Excel header detection and Access upload, filters, every assistant tool, the dispatcher, both routers, the number check, the backup model and the Gemini fallback (with a fake client, so the tests run offline).

## Limitations

- **Revenue is not profit.** There are no costs or margins, so the app cannot say which stores or products are most profitable.
- **No causes.** The data shows what happened and what moves together, not why. For example, website revenue more than doubled while in-store revenue fell, but the data cannot say why.
- **Monthly totals only.** Sales are monthly totals per store and product, so there is no customer, transaction or daily detail.
- **Channel is a mapping.** The data has no channel column; channel is the store type, and the website is treated as one store.
- **Satisfaction is thin evidence.** It is one annual score per store, the website is not surveyed, and the correlation with revenue is weak (0.235).
- **Gemini availability.** The free tier allows about 20 requests per model per day, and each question uses two. When both models are out of quota or busy, the rules-based assistant answers.

## Files

| File | Purpose |
|---|---|
| `streamlit_app.py` | Screen layout and interaction |
| `analytics.py` | Loading (CSV, Excel, Access), cleaning, merge checks, metrics and analytical tools (no Streamlit code) |
| `agent.py` | Tool registry, dispatcher, rules router, Gemini router and explanation, number check |
| `tests/` | Tests with hand-checkable answers |
| `scripts/export_access.py` | Export of the Access tables to CSV |
| `scripts/independent_check.py` | Independent recalculation of the validation measures with plain pandas |
| `docs/` | Data profile, Power BI reconciliation, validation evidence, agent tests, reflection draft, team contribution |
| `requirements.txt` | Packages the hosted app needs |

## Run it

Hosted: deploy from this repository on share.streamlit.io with main file `streamlit_app.py` and Python 3.11 or newer.

Locally (optional): `pip install -r requirements-dev.txt`, then `streamlit run streamlit_app.py`. Run the tests with `pytest`.

## Secrets

Never commit API keys. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` (which git ignores) and add `GEMINI_API_KEY`. On Streamlit Community Cloud, paste the same line into App settings → Secrets. Without a key, the app runs the rules-based assistant.
