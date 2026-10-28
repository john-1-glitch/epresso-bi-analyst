# E-Presso BI Analyst & AI Agent

University assignment: a Streamlit BI app over the E-Presso sales data, plus a safe, tool-based analytical assistant. Build incrementally, one checkpoint at a time. Never write the whole app at once, and ask for confirmation before moving to the next step.

## Folder layout

- `streamlit_app.py` — UI only: layout, sidebar filters, charts, assistant chat.
- `analytics.py` — all data loading, cleaning, merges, calculations and analytical tools (no Streamlit code).
- `agent.py` — tool registry (`TOOLS`), dispatcher (`run_tool`), rules and LLM routers. From the professor's starter repo.
- `tests/` — pytest unit tests on tiny synthetic DataFrames verifiable by hand.
- `data/raw/` — E-Presso source CSV/XLSX files, unmodified.
- `docs/` — grain table, data profile, reconciliation notes for the oral defense.
- `.streamlit/secrets.toml` — API keys (gitignored, never committed).

## Build order (professor's checkpoints)

1. Inspect repo and data files; produce grain statements and data profile.
2. Compute Total Revenue with merge checks; reconcile against Power BI numbers supplied by the user.
3. Dynamic filters and 3 manager-ready charts.
4. Unit tests on a tiny synthetic DataFrame.
5. Analytical functions, tool contract, dispatcher and rules-based router.
6. LLM function-calling upgrade (only if requested).

After writing any function, explain it in plain English for the oral defense.

## Data rules

- Grain table before any join: Table | Source | One row = ___ | Key column(s) | Row count | Contains revenue?
- Wrap every merge in `check_merge()`, logging rows and revenue sum before/after. Revenue must never duplicate or drop.
- No hardcoding: filter options (years, stores, categories, channels), totals and chart values all come from the loaded data.
- Handle blanks/NaN without crashing; report them in a Data Quality section. Missing required columns show a clean UI message naming the column, never a traceback.
- `@st.cache_data` must key on the uploaded file's contents (e.g. bytes or hash) so a modified CSV (+$1,000 test) updates everything without a restart.

## Assistant rules

Prohibited: LangChain pandas agent, PandasAI, code interpreters, `eval()`/`exec()` on LLM output, free-form model-written SQL or pandas, and any tool that mutates loaded data.

Allowed: at least 3 tested functions in `analytics.py` with explicit parameters (`year`, `channel`, `store`) that never read Streamlit state. Validate parameters against loaded values and return a clean error for unknowns. A central dispatcher maps tool names to functions, executes them and logs every call; rules-based router first, LLM router only via strict tool-use over the fixed tool set, stating only numbers from tool results.

Every tool returns exactly:

```python
{
    "tool": "function_name",
    "value": {...},
    "definition": "How the metric is calculated",
    "scope": {"year": "...", "store": "...", "channel": "..."},
    "rows_used": int,
    "caveats": ["Revenue is not profit", "..."],
}
```

Assistant UI: every answer opens with its scope ("Scope: all stores, all years"), then Findings (tool results only), Interpretation, and Next steps (or "no recommendation is supported by this data"). Show a Tool-Call Log (function, arguments, raw JSON) under each answer. Limit ~15 questions per session with a clear message. Fall back to the rules-based router if the LLM API fails. Reject unsupported questions (profit, causation) and say what can be answered. API keys only via `st.secrets`.
