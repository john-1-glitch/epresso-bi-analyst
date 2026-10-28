# Agent Testing Evidence

All tests ran against the original E-Presso files (the Access database, the Stores CSV and the Survey workbook), with Gemini as the language model. Each answer's tool-call log records the function, its arguments and the raw result. Numerical results were checked independently with plain pandas (`scripts/independent_check.py` and a store-change pivot table). Before submitting, rerun Tests 1, 2 and 5 on the hosted app and attach screenshots of their tool-call logs.

## Agent test table

| Test | Question asked | Function(s) used | Expected result / behavior | Actual result | Pass / Fail | Revision made |
|---|---|---|---|---|---|---|
| 1. Numerical calculation | How much revenue did the website generate in 2028? | `total_revenue(year=2028, channel="Website")` | $6,339,640 (independent pandas check: 6,339,640.0) | "In 2028, the website generated $6,339,640 in total revenue across 240 sales rows. This represents 15.03% of all revenue." Scope: all stores, year 2028, Website. | Pass | None |
| 2. Numerical comparison | Which store experienced the greatest percentage revenue decline? | `revenue_change_by_store()` (default 2025 vs 2029) | SH11 Chicago, $539,740 to $179,130, −66.81% (independent pivot: SH11, −66.81; 20 of 21 stores declined). If no store declined, the assistant must say so instead of naming one. | "SH11 · Chicago (Midwest) experienced the greatest percentage revenue decline, with revenue falling from $539,740 in 2025 to $179,130 in 2029 ... a −66.81% decline." | Pass (after fix 1) | Fix 1 below: the earlier version had no store-change function |
| 3. Missing information | Which E-Presso store is the most profitable? | None (refused before any tool or model call) | Refuse: revenue alone cannot measure profit; say what it can answer instead. | "I cannot answer that one. The data has revenue only, with no costs, so profit, margins and profitability cannot be calculated. Revenue is not profit." followed by the list of supported questions. Next steps: "No recommendation is supported by this data." | Pass | None |
| 4. Analytical judgment | Does higher customer satisfaction cause higher revenue? | `satisfaction_vs_revenue()` | Show the association with real numbers, then say it does not prove causation; note that store-years without a score are excluded, not counted as zero. | Correlation 0.235 across 100 store-years (5 website store-years without a score left out), average satisfaction 3.4. Interpretation: "a weak association ... does not prove that higher customer satisfaction causes higher revenue." Caveats include "Correlation is not causation" and the excluded-scores note. | Pass | Earlier versions refused every "cause" question outright; the assistant now shows the evidence with the limit stated |
| 5. Updated data | How much revenue did the website generate in 2025? (asked before and after uploading `Sales_plus1000.csv`) | `total_revenue(year=2025, channel="Website")` | Before $3,160,230; after $3,161,230 (+$1,000 on row S0020250101) | Before: "Revenue: $3,160,230.00 from 240 sales rows." After: "Revenue: $3,161,230.00 from 240 sales rows." Same function and arguments both times; only the loaded data changed. | Pass | None |
| Extra: fix and retest | Should E-Presso close its Chicago store? and Which sales channel experienced the greatest revenue growth? | `revenue_by_year(store="Chicago")`; `revenue_change_by_channel()` | Show Chicago's trend but give no recommendation, naming the missing information; Website grew most (+121.61%) | Chicago: $539,740 (2025) to $179,130 (2029), −66.81%; Next steps: "No recommendation is supported by this data. A decision like this would also need operating expenses and store profitability, lease obligations, staffing ..." Channel: Website +121.61%, Brick & Mortar −45.83%. | Pass (after fix 2) | Fix 2 below: Gemini quota and overload |

## Documented fixes and retests

### Fix 1: a comparison question was answered with the wrong function

When we first ran Test 2 on the earlier version of the assistant (commit `c6e9e7e`), the router matched the words "greatest" and "store" and chose `top_stores`, which ranks stores by total revenue. The answer listed SH00 Website at $27,218,880 as the top store. That was a correct number for the wrong question, because nothing in the tool set could measure a change between years.

We added two tested functions, `revenue_change_by_store` and `revenue_change_by_channel`. They compare each store or channel between two years (by default the first and last year in the loaded data), validate the years, leave out stores missing from either year, and say plainly when no store declined. We also added routing rules for "greatest decline/growth" questions. On retest, both Gemini and the rules router chose `revenue_change_by_store` and returned SH11 Chicago at −66.81%, which matches the independent pivot table. Unit tests cover the no-decline case.

### Fix 2: Gemini's free quota ran out mid-testing

During the first full test run, Gemini returned "503 UNAVAILABLE" (high demand) and then "429 RESOURCE_EXHAUSTED". The free tier allows only 20 requests per model per day, and each question uses two requests: one to choose the function and one to write the explanation. The app handled it gracefully, because the rules-based assistant answered with a visible note, but most answers lost the language-model explanation. The same could happen while the instructor is grading.

We added one retry on a second model (`gemini-3.5-flash-lite`), which has its own quota, whenever the main model returns 429 or 503. Other errors still fall back to the rules-based assistant immediately. On retest, with the main model's daily quota still used up, both extra questions were answered by `gemini-3.5-flash-lite`, which chose the function and wrote the explanation. The tool-call log records which model answered. Unit tests cover the retry and the immediate fallback for other errors.

### Earlier fixes found by testing

The first Gemini model we configured (`gemini-2.5-flash`) returned "404: no longer available to new users". The rules router answered every question until we switched to `gemini-3.5-flash`. Testing also showed the rules router missed phrasings like "Are there blanks in the data?", "How much money did we make?" and "Which three locations brought in the most?". We added those patterns and retested them in `tests/test_tools.py`.

## How we know the answers come from real calculations

Every answer's tool-call log shows the function name, its arguments and the raw JSON result, including `rows_used`. The scope line, the caveats and the decision limits are written by our code, not by the model. When Gemini words the explanation, the app checks every number in its text against the tool result (allowing for rounding, millions and percentages). If any number cannot be traced, the app shows the standard answer instead and says why. Test 5 is the direct proof: the same question and the same function returned a $1,000 higher answer as soon as the modified file was uploaded.
