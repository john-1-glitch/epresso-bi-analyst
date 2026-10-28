# E-Presso BI Analyst

## What this application does

E-Presso BI Analyst lets a manager upload the company's sales, store and survey files and see where revenue comes from: by year, by channel (website or physical stores), by product category and by store. It recalculates everything from the uploaded files, so a corrected or updated file changes every number on screen without touching the code. A built-in assistant answers plain-English questions such as "Is the online shop growing?" by running one of a small set of approved calculations and showing exactly what it ran.

## Live app

_Add the Streamlit Community Cloud URL here after deploying._ The app may take a moment to wake up if nobody has used it recently.

## How to use it

Upload one file per table in the sidebar. The app checks each file for the columns it needs and names any missing column instead of crashing.

| Upload box | File used for this project | Notes |
|---|---|---|
| Sales | `Sales.csv` | Exported from `Support_EX_B_Sales.accdb` with `scripts/export_access.py` |
| Stores | `Support_EX_B_Stores.csv` | Tab-separated; the app detects the delimiter |
| Categories | `Categories.csv` | Exported from the Access database |
| Products | `Products.csv` | Exported from the Access database |
| Survey Results (optional) | `Support_EX_B_Survey.xlsx` | The app finds the "Survey Results" sheet and its header row on its own |

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

The assistant can run in two modes, and every answer says which one answered.

- **Gemini mode (when `GEMINI_API_KEY` is set):** Gemini reads the question and chooses one approved function and its arguments through function calling in `VALIDATED` mode, which constrains it to the listed functions and their schemas. Gemini never sees the data and never writes a number. Our code runs the function and builds the answer from its result.
- **Rules-based mode (no key, or Gemini fails):** keyword rules choose the function. If Gemini hits a quota limit, a server error or a bad key, the rules router answers automatically and the answer shows a note saying so.

| Tool | What it calculates | Arguments |
|---|---|---|
| `total_revenue` | Revenue in scope and its share of all revenue | year, channel, store |
| `revenue_by_year` | Revenue for each year and the change from first to last year | channel, store |
| `revenue_by_category` | Revenue and share for each product category | year, channel, store |
| `revenue_by_channel` | Revenue and share for Website vs Brick & Mortar | year, store |
| `top_stores` | The n stores with the most revenue, with shares | year, channel, n |
| `data_quality_report` | Rows, duplicates and missing values | none |

Every tool checks its arguments against the loaded data and returns a clear error for an unknown year, channel or store, or for a city that matches two stores; it never guesses. Every result follows the same contract: `tool`, `value`, `definition`, `scope`, `rows_used` and `caveats`. Each answer starts with its scope, then shows Findings (numbers from the tool result only), Interpretation, Next steps and an expandable tool-call log with the raw result.

**What the assistant is not allowed to do:** it cannot run or write code, use `eval()` or `exec()`, write SQL or pandas, change the loaded data, or answer questions the data cannot support. Questions about profit, causes ("why"), forecasts or customers are refused before any model is called, with the reason and a list of what it can answer.

## Validation

**Power BI comparison.** The data model inside the Power BI report (`Fleischnamnn_Mey_NP_EX_B_EPresso.pbix`) was read directly and compared with the app's numbers. They match exactly: total revenue is $42,174,440, every year matches (2025: $7,181,750 through 2029: $9,181,550), and all 25,200 sales rows have identical revenue. Details: [`docs/powerbi_reconciliation.md`](docs/powerbi_reconciliation.md).

**Independent check.** Revenue was summed separately from the original Access database, from the exported CSV and from the Power BI model. All three agree, and the monthly shares match the Power BI column chart (for example, December is 25.61% of revenue).

**$1,000 test.** Adding $1,000 to one Sales row and uploading the changed file raises total revenue to exactly $42,175,440 without restarting the app, because the cache is keyed on each file's contents.

**Automated tests.** 59 tests in `tests/` run on a six-row dataset whose answers are worked out by hand in the test file. They cover totals, merge checks (including a duplicated store key and an unknown store), missing columns, Excel header detection, filters, every assistant tool, the dispatcher, both routers and the Gemini fallback (with a fake client, so the tests run offline).

## Limitations

- **Revenue is not profit.** There are no costs or margins, so the app cannot say which stores or products are most profitable.
- **No causes.** The data shows what happened, not why. For example, website revenue more than doubled while in-store revenue fell, but the data cannot say why.
- **Monthly totals only.** Sales are monthly totals per store and product, so there is no customer, transaction or daily detail.
- **Channel is a mapping.** The data has no channel column; channel is the store type, and the website is treated as one store.
- **Survey data is shown, not modeled.** Satisfaction scores are profiled but not used to explain revenue.
- **Gemini availability.** The free Gemini tier allows only a few requests per minute and is sometimes busy; the rules-based assistant then answers instead.

## Files

| File | Purpose |
|---|---|
| `streamlit_app.py` | Screen layout and interaction |
| `analytics.py` | Loading, cleaning, merge checks, metrics and analytical tools (no Streamlit code) |
| `agent.py` | Tool registry, dispatcher, rules router and Gemini router |
| `tests/` | Tests with hand-checkable answers |
| `scripts/export_access.py` | One-off export of the Access tables to CSV |
| `docs/` | Data profile, grain statements and Power BI reconciliation |
| `requirements.txt` | Packages the hosted app needs |

## Run it

Hosted: deploy from this repository on share.streamlit.io with main file `streamlit_app.py` and Python 3.11 or newer.

Locally (optional): `pip install -r requirements-dev.txt`, then `streamlit run streamlit_app.py`. Run the tests with `pytest`.

## Secrets

Never commit API keys. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` (which git ignores) and add `GEMINI_API_KEY`. On Streamlit Community Cloud, paste the same line into App settings → Secrets. Without a key, the app runs the rules-based assistant.
