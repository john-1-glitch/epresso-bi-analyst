"""Assistant: a dispatcher plus a router.
The router (rules now, a language model later) only CHOOSES a tool and its
arguments. Our code RUNS the tool. Nothing here executes model-written code."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import analytics
from analytics import ToolResult

logger = logging.getLogger(__name__)

# The ONLY things the assistant may do. Each entry names the function and the
# arguments it accepts; anything else is rejected by the dispatcher.
TOOLS = {
    'total_revenue': {
        'fn': analytics.total_revenue,
        'params': {'year', 'channel', 'store'},
        'description': 'Total revenue, optionally for one year, channel and/or store.',
    },
    'revenue_by_year': {
        'fn': analytics.revenue_by_year,
        'params': {'channel', 'store'},
        'description': 'Revenue for each year and the change from first to last year.',
    },
    'revenue_by_category': {
        'fn': analytics.revenue_by_category,
        'params': {'year', 'channel', 'store'},
        'description': 'Revenue and share for each product category.',
    },
    'revenue_by_channel': {
        'fn': analytics.revenue_by_channel,
        'params': {'year', 'store'},
        'description': 'Revenue and share for Website vs Brick & Mortar.',
    },
    'top_stores': {
        'fn': analytics.top_stores,
        'params': {'year', 'channel', 'n'},
        'description': 'The n stores with the most revenue.',
    },
    'data_quality_report': {
        'fn': analytics.data_quality_report,
        'params': set(),
        'description': 'Report duplicates and missing values in the loaded data.',
    },
}

CAN_ANSWER = ('I can answer: total revenue; revenue by year, by category or by channel '
              '(Website vs Brick & Mortar); the top stores by revenue; and data quality. '
              'Each can be narrowed to a year, a channel or a store.')
UNSUPPORTED = ('I cannot answer that one with my approved analytical functions '
               f'({", ".join(TOOLS)}). ' + CAN_ANSWER)
NO_RECOMMENDATION = 'No recommendation is supported by this data.'

# Questions the data cannot support, with the reason given to the user.
REJECTIONS = [
    (r'\b(profit|margin|cost|costs|expense|net income|earnings)\b',
     'The data has revenue only, with no costs, so profit and margins cannot be calculated.'),
    (r'\b(why|cause|caused|causes|because|impact of|effect of|driv(e|es|en|ing))\b',
     'The data shows what happened, not why. It cannot establish causes.'),
    (r'\b(forecast|predict|projection|next year|future)\b',
     'Forecasting is not one of my functions; I only report what is in the loaded data.'),
    (r'\b(customer|customers|basket|transaction|transactions)\b',
     'Sales are monthly totals per store and product, so there is no customer-level data.'),
]

# For when you add a language model: keep these rules in the system prompt.
SYSTEM_PROMPT = """You are an analytical assistant for E-Presso managers.
Rules:
- Only state numbers that appear in a tool result. Never estimate or recall figures.
- Always call a tool before making a numerical claim.
- State the scope (which filters/period) of every answer.
- Separate Findings (from tool results), Interpretation, and Next steps.
- Revenue is not profit. Association is not causation.
- If the data cannot support a conclusion, say so and name the missing information.
- Never ask for, run, or write code. Never modify data."""


def run_tool(name: str, args: dict, df, log: list) -> ToolResult:
    """The dispatcher: validate the request, run the function, log the call."""
    if name not in TOOLS:
        result = analytics._error(name, f'Unknown tool: {name}')
    else:
        unexpected = set(args) - TOOLS[name]['params']
        if unexpected:
            result = analytics._error(name, f'Unexpected argument(s): {sorted(unexpected)}')
        else:
            result = TOOLS[name]['fn'](df, **args)
    log.append({'tool': name, 'arguments': args, 'result': result.to_dict(),
                'called_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
    logger.info('tool call %s(%s) -> %s', name, args, 'error' if result.error else 'ok')
    return result


# ------------------------------------------------------------- rules router
def rejection_reason(question: str):
    """Why the data cannot answer this question, or None if it might."""
    q = question.lower()
    for pattern, reason in REJECTIONS:
        if re.search(pattern, q):
            return reason
    return None


def extract_scope(question: str, df) -> dict:
    """Pull year, channel and store out of the question. Values are passed on as
    written; the tool itself checks them against the data and rejects unknown ones."""
    q = question.lower()
    args = {}
    year = re.search(r'\b(19|20)\d{2}\b', q)
    if year:
        args['year'] = int(year.group())
    if re.search(r'\b(website|online|web|e-?commerce)\b', q):
        args['channel'] = 'Website'
    elif re.search(r'\b(brick|mortar|physical|in-store|shops?)\b', q):
        args['channel'] = 'Brick & Mortar'
    store_id = re.search(r'\bsh\d{2}\b', q)
    if store_id:
        args['store'] = store_id.group().upper()
    elif 'City' in df.columns:
        cities = sorted(df['City'].dropna().unique(), key=len, reverse=True)
        city = next((c for c in cities if re.search(rf'\b{re.escape(c.lower())}\b', q)), None)
        if city:
            args['store'] = city
    return args


def rules_router(question: str, df):
    """Option B router: keyword rules pick ONE tool from TOOLS and its arguments.
    Returns (tool_name, arguments) or None if the question is unsupported."""
    q = question.lower()
    if re.search(r'\b(missing|duplicate|quality|blank|null)\b', q):
        return 'data_quality_report', {}
    scope = extract_scope(question, df)
    if re.search(r'\b(categor(y|ies)|product line|products?)\b', q):
        name = 'revenue_by_category'
    elif re.search(r'\b(top|best|highest|largest|biggest|rank|ranking)\b.*\bstores?\b'
                   r'|\bwhich stores?\b', q):
        name = 'top_stores'
        n = re.search(r'\btop\s+(\d+)\b', q)
        scope['n'] = int(n.group(1)) if n else 5
    elif re.search(r'\b(by channel|channels|channel split|split|vs|versus|compare|compared)\b', q):
        name = 'revenue_by_channel'
    elif re.search(r'\b(trend|grow|grew|grown|growth|growing|over time|by year|per year|each year'
                   r'|yearly|annual|declin\w*)\b', q):
        name = 'revenue_by_year'
    elif re.search(r'\b(revenue|sales|total|sell|sold|turnover)\b', q):
        name = 'total_revenue'
    else:
        return None
    allowed = TOOLS[name]['params']
    return name, {k: v for k, v in scope.items() if k in allowed}


def llm_router(question: str, df):
    """Option A router. TODO: after the rules-based version works, ask a model that
    supports function calling to choose from TOOLS. Return (name, args) or None."""
    raise NotImplementedError('Add the language-model router after your tools are tested.')


# ---------------------------------------------------------------- answers
def _money(x) -> str:
    return f'${x:,.2f}'


def _pct(x) -> str:
    return 'n/a' if x is None else f'{x:.1%}'


def describe_scope(scope: dict) -> str:
    if not scope:
        return 'Scope: not applicable'
    year = scope.get('year', 'all years')
    year = f'year {year}' if isinstance(year, int) else year
    return f"Scope: {scope.get('store', 'all stores')}, {year}, {scope.get('channel', 'all channels')}"


def findings_and_interpretation(result: ToolResult):
    """Turn a tool result into Findings (only numbers from the result) and a
    one-sentence Interpretation computed from those same numbers."""
    v = result.value
    if result.tool == 'total_revenue':
        return (f"Revenue: {_money(v['revenue'])} from {result.rows_used:,} sales rows.",
                f"That is {_pct(v['share_of_all_revenue'])} of all revenue in the loaded data.")
    if result.tool == 'revenue_by_year':
        years = '; '.join(f'{y}: {_money(x)}' for y, x in v['by_year'].items())
        change = v['change_first_to_last_pct']
        first, last = list(v['by_year'])[0], list(v['by_year'])[-1]
        if change is None:
            return years, 'Only one year is in scope, so there is no change to describe.'
        word = 'rose' if change >= 0 else 'fell'
        return years, f'Revenue {word} {abs(change):.1f}% from {first} to {last}.'
    if result.tool in ('revenue_by_category', 'revenue_by_channel'):
        key = 'by_category' if result.tool == 'revenue_by_category' else 'by_channel'
        parts = v[key]
        lines = '; '.join(f"{k}: {_money(x['revenue'])} ({_pct(x['share'])})" for k, x in parts.items())
        top, top_v = next(iter(parts.items()))
        noun = 'category' if key == 'by_category' else 'channel'
        return lines, f"The largest {noun} is {top}, with {_pct(top_v['share'])} of revenue in this scope."
    if result.tool == 'top_stores':
        lines = '; '.join(f"{s['rank']}. {s['store']}: {_money(s['revenue'])} ({_pct(s['share'])})"
                          for s in v['stores'])
        best = v['stores'][0]
        return lines, (f"The top store, {best['store']}, accounts for {_pct(best['share'])} of "
                       f"revenue across the {v['stores_in_scope']} stores in scope.")
    if result.tool == 'data_quality_report':
        missing = v['columns_with_missing']
        cols = ', '.join(f'{c} ({n})' for c, n in missing.items()) or 'none'
        return (f"{v['rows']:,} rows; {v['duplicate_rows']} duplicate rows; columns with missing "
                f'values: {cols}.',
                'Blank values are reported, not filled in, so totals only use recorded values.')
    return f'{result.definition}: {result.value}', ''


NEXT_STEPS = {
    'total_revenue': 'Break this total down by category, channel or year for the same scope.',
    'revenue_by_year': 'Compare channels for the first and last year to see where the change came from.',
    'revenue_by_category': 'Check whether the category mix differs between the Website and Brick & Mortar.',
    'revenue_by_channel': 'Look at revenue by year for each channel to see how the split has moved.',
    'top_stores': 'Ask for one of these stores by ID to see its categories or yearly trend.',
}


def answer_question(question: str, df, mode: str = 'rules') -> dict:
    log: list = []
    reason = rejection_reason(question)
    routed = None if reason else (rules_router(question, df) if mode == 'rules'
                                  else llm_router(question, df))
    if routed is None:
        findings = (f'I cannot answer that one. {reason} {CAN_ANSWER}' if reason else UNSUPPORTED)
        return {'scope': describe_scope({}), 'findings': findings, 'interpretation': '',
                'caveats': [], 'next_steps': NO_RECOMMENDATION, 'tool_log': log}
    name, args = routed
    result = run_tool(name, args, df, log)
    if result.error:
        return {'scope': describe_scope(result.scope),
                'findings': f'The request could not be completed: {result.error}',
                'interpretation': '', 'caveats': [], 'next_steps': NO_RECOMMENDATION,
                'tool_log': log}
    findings, interpretation = findings_and_interpretation(result)
    return {
        'scope': describe_scope(result.scope),
        'findings': findings,
        'interpretation': interpretation,
        'caveats': result.caveats,
        'next_steps': NEXT_STEPS.get(name, NO_RECOMMENDATION),
        'tool_log': log,
    }
