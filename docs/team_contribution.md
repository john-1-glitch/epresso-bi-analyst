# Team Contribution Statement (fill in before submitting)

> The Project Guide requires commits from every team member and asks each person to explain a key calculation or design decision. Fill in both rows. Count commits with `git shortlog -sn` once both of you have committed.

| Team member | Main responsibilities | Key calculation or design decision I can explain | Approximate commits |
|---|---|---|---|
| Jonathan Mey | Data loading and merge checks, Power BI reconciliation, dashboard, analytical functions, Gemini integration, testing and documentation (with Claude Code) | Why Survey Results is not joined to Sales (different grain: store-year vs. product-store-month), and how `check_merge()` proves a join kept 25,200 rows and $42,174,440 | |
| _Teammate name_ | | | |

## Suggested topics for each person's defense

- How total revenue is calculated, and why it matches Power BI.
- How the app recalculates after a modified upload (the cache is keyed on file contents).
- What the dispatcher does, and why the model can only choose among named functions.
- How the number check stops Gemini from stating a figure the tools did not calculate.
- Why the assistant refuses profit questions and adds limits to "should we" and "does X cause Y" questions.
