"""Assistant: a dispatcher plus a router.
The router (keyword rules, or Gemini) only CHOOSES a tool and its arguments. Our code
RUNS the tool. Gemini may then word the explanation, but every number it writes is
checked against the tool result. Nothing here executes model-written code."""
from __future__ import annotations

import json
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
        'description': ('Revenue for each year, the best and worst year, and the change from '
                        'first to last year.'),
    },
    'monthly_revenue_share': {
        'fn': analytics.monthly_revenue_share,
        'params': {'year', 'channel', 'store'},
        'description': ('Revenue and share for each calendar month, with the strongest and '
                        'weakest month (seasonality).'),
    },
    'revenue_by_category': {
        'fn': analytics.revenue_by_category,
        'params': {'year', 'channel', 'store'},
        'description': 'Revenue and share for each product category.',
    },
    'revenue_by_channel': {
        'fn': analytics.revenue_by_channel,
        'params': {'year', 'store'},
        'description': 'Revenue and share for Website vs Brick & Mortar in one period.',
    },
    'top_stores': {
        'fn': analytics.top_stores,
        'params': {'year', 'channel', 'n'},
        'description': 'The n stores with the most revenue.',
    },
    'revenue_change_by_store': {
        'fn': analytics.revenue_change_by_store,
        'params': {'start_year', 'end_year', 'channel'},
        'description': ('Each store\'s revenue change between two years (default: first vs last '
                        'year), with the largest percentage decline and growth.'),
    },
    'revenue_change_by_channel': {
        'fn': analytics.revenue_change_by_channel,
        'params': {'start_year', 'end_year', 'store'},
        'description': ('Each channel\'s revenue change between two years (default: first vs '
                        'last year), and which channel grew the most.'),
    },
    'satisfaction_vs_revenue': {
        'fn': analytics.satisfaction_vs_revenue,
        'params': {'year'},
        'needs_survey': True,
        'description': ('Correlation between store revenue and customer satisfaction survey '
                        'scores (association only, not causation).'),
    },
    'data_quality_report': {
        'fn': analytics.data_quality_report,
        'params': set(),
        'description': 'Report duplicates and missing values in the loaded data.',
    },
}

CAN_ANSWER = ('I can answer: total revenue; revenue by year (including the best and worst '
              'year), by month, by category or by channel '
              '(Website vs Brick & Mortar); the top stores; how each store or channel changed '
              'between two years; how satisfaction scores relate to revenue; and data quality. '
              'Most can be narrowed to a year, a channel or a store.')
UNSUPPORTED = ('I cannot answer that one with my approved analytical functions '
               f'({", ".join(TOOLS)}). ' + CAN_ANSWER)
NO_RECOMMENDATION = 'No recommendation is supported by this data.'
DECISION_GAPS = ('A decision like this would also need operating expenses and store '
                 'profitability, lease obligations, staffing, local market conditions and '
                 'customer behavior, none of which are in this data.')
CAUSAL_CAVEAT = ('This data can show what happened and what moves together, not why. It '
                 'cannot establish that one thing causes another.')
FULL_DATA_NOTE = 'full loaded dataset; dashboard filters are not applied'

# Questions the data cannot answer at all: refused before any tool or model is used.
REJECTIONS = [
    (r'\b(profit|profits|profitable|profitability|margins?|costs?|expenses?|net income'
     r'|earnings|roi)\b',
     'The data has revenue only, with no costs, so profit, margins and profitability cannot '
     'be calculated. Revenue is not profit.'),
    (r'\b(forecast|predict|prediction|projection|next year|future)\b',
     'Forecasting is not one of my functions; I only report what is in the loaded data.'),
    (r'\b(customer count|number of customers|basket|transactions?|per customer)\b',
     'Sales are monthly totals per store and product, so there is no customer-level data.'),
]
# Questions the data can partly answer: the evidence is shown with an explicit limit.
CAUSAL = r'\b(why|cause|caused|causes|causing|because|impact of|effect of|driv(e|es|en|ing))\b'
DECISION = (r'\b(should|recommend\w*|close|closing|shut|open a|invest\w*|expand\w*|cut|keep'
            r'|worth it)\b')

SYSTEM_PROMPT = """You are an analytical assistant for E-Presso managers.
Rules:
- Only state numbers that appear in a tool result. Never estimate or recall figures.
- Always call a tool before making a numerical claim.
- State the scope (which filters/period) of every answer.
- Separate Findings (from tool results), Interpretation, and Next steps.
- Revenue is not profit. Association is not causation.
- If the data cannot support a conclusion, say so and name the missing information.
- Never ask for, run, or write code. Never modify data."""

ROUTER_PROMPT = SYSTEM_PROMPT + """

Your job now: choose exactly ONE of the provided functions, with arguments, that gives the
best evidence for the manager's question about E-Presso. Only fill in an argument when the
question states it. For "why", "does X cause Y" or "should we..." questions, still choose the
function that shows the relevant evidence; the limits are explained to the manager separately.
If no function provides relevant evidence, do not call one; reply with one short sentence."""

EXPLAIN_PROMPT = SYSTEM_PROMPT + """

Your job now: explain ONE tool result to an E-Presso manager, as JSON with the keys
findings, interpretation and next_steps.
- findings: 1-3 sentences stating the facts in the tool result. Every number you write must
  appear in the tool result. You may round money to whole dollars or to millions with one
  decimal (e.g. $9.2M) and show shares as percentages (0.645 -> 64.5%). Add no other numbers.
- interpretation: 1-2 sentences on what the facts suggest, consistent with every caveat.
  Never say or imply that revenue shows profit, or that one thing causes another.
- next_steps: one further analysis the manager could run with THIS data, or exactly
  "No recommendation is supported by this data." when the question asks for a decision.
  The data only has monthly revenue by store, channel, product and category, plus one
  annual satisfaction score per store. There are no transactions, orders, customers or costs.
- Write money with thousands separators and no trailing ".0" (e.g. $3,843,020).
Do not repeat the scope or the caveats; the app shows them."""

EXPLAIN_SCHEMA = {
    'type': 'object',
    'properties': {'findings': {'type': 'string'}, 'interpretation': {'type': 'string'},
                   'next_steps': {'type': 'string'}},
    'required': ['findings', 'interpretation', 'next_steps'],
}

GEMINI_MODEL = 'gemini-3.5-flash'  # default; override with GEMINI_MODEL in Streamlit secrets
# The free tier allows only about 20 requests per model per day and the main model is
# sometimes overloaded, so a busy (503) or out-of-quota (429) model gets one retry on this
# separate model before the app falls back to the rules-based assistant.
GEMINI_BACKUP_MODEL = 'gemini-3.5-flash-lite'


def _with_backup(call, model: str):
    """Run call(model). If Gemini is overloaded or the free quota is used up, try the
    backup model once. Returns (result, model_used); any other error is raised."""
    try:
        return call(model), model
    except Exception as exc:
        if getattr(exc, 'code', None) not in (429, 503) or model == GEMINI_BACKUP_MODEL:
            raise
        logger.warning('%s unavailable (%s); trying %s', model, exc, GEMINI_BACKUP_MODEL)
        return call(GEMINI_BACKUP_MODEL), GEMINI_BACKUP_MODEL


def run_tool(name: str, args: dict, df, log: list, survey=None) -> ToolResult:
    """The dispatcher: validate the request, run the function, log the call."""
    if name not in TOOLS:
        result = analytics._error(name, f'Unknown tool: {name}')
    else:
        unexpected = set(args) - TOOLS[name]['params']
        if unexpected:
            result = analytics._error(name, f'Unexpected argument(s): {sorted(unexpected)}')
        elif TOOLS[name].get('needs_survey'):
            result = TOOLS[name]['fn'](df, survey=survey, **args)
        else:
            result = TOOLS[name]['fn'](df, **args)
    log.append({'tool': name, 'arguments': args, 'result': result.to_dict(),
                'called_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
    logger.info('tool call %s(%s) -> %s', name, args, 'error' if result.error else 'ok')
    return result


# ------------------------------------------------------------- rules router
def rejection_reason(question: str):
    """Why the data cannot answer this question at all, or None if it might."""
    q = question.lower()
    for pattern, reason in REJECTIONS:
        if re.search(pattern, q):
            return reason
    return None


def question_flags(question: str) -> dict:
    """Is this a causal question, a decision question, or both?"""
    q = question.lower()
    return {'causal': bool(re.search(CAUSAL, q)), 'decision': bool(re.search(DECISION, q))}


def extract_scope(question: str, df) -> dict:
    """Pull years, channel and store out of the question. Values are passed on as
    written; the tool itself checks them against the data and rejects unknown ones."""
    q = question.lower()
    args = {}
    years = [int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', q)]
    if years:
        args['year'] = years[0]
    if len(years) >= 2:
        args['start_year'], args['end_year'] = min(years), max(years)
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


CHANGE = r'\b(declin\w*|drop\w*|fell|fall\w*|decreas\w*|grow\w*|grew|growth|increas\w*|rose|change\w*)\b'
RANKED = r'\b(which|greatest|largest|biggest|most|fastest|best|worst|steepest)\b'
PLACES = r'\b(stores?|locations?|shops?)\b'
CHANNEL_WORDS = r'\b(channels?|website|online|brick|mortar|physical)\b'


def rules_router(question: str, df):
    """Option B router: keyword rules pick ONE tool from TOOLS and its arguments.
    Returns (tool_name, arguments) or None if the question is unsupported."""
    q = question.lower()
    if re.search(r'\b(missing|duplicates?|quality|blanks?|nulls?)\b', q):
        return 'data_quality_report', {}
    scope = extract_scope(question, df)
    if re.search(r'\b(satisf\w*|survey|happy|happier)\b', q):
        name = 'satisfaction_vs_revenue'
    elif re.search(r'\b(months?|monthly|season\w*|christmas|holiday)\b', q):
        name = 'monthly_revenue_share'
    elif re.search(r'\b(which|what)\b.*\byears?\b|\b(best|worst|highest|lowest|strongest'
                   r'|weakest|biggest|top|record)\b.*\byears?\b', q):
        name = 'revenue_by_year'
    elif re.search(CHANGE, q) and re.search(RANKED, q) and re.search(CHANNEL_WORDS, q):
        name = 'revenue_change_by_channel'
    elif re.search(CHANGE, q) and re.search(RANKED, q) and re.search(PLACES, q) and 'store' not in scope:
        name = 'revenue_change_by_store'
    elif re.search(r'\b(categor(y|ies)|product line|products?|sells? best|best[- ]selling)\b', q):
        name = 'revenue_by_category'
    elif re.search(r'\b(top|best|highest|largest|biggest|most|rank|ranking)\b.*' + PLACES
                   + r'|\bwhich\b.*' + PLACES, q):
        name = 'top_stores'
        words = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8,
                 'nine': 9, 'ten': 10}
        counts = [w for w in re.findall(r'\b(\d+|' + '|'.join(words) + r')\b', q)
                  if not re.fullmatch(r'(19|20)\d{2}', w)]  # skip years
        scope['n'] = int(words.get(counts[0], counts[0])) if counts else 5
    elif re.search(r'\b(by channel|channels|channel split|split|vs|versus|compare|compared)\b', q):
        name = 'revenue_by_channel'
    elif re.search(r'\b(trend|over time|by year|per year|each year|yearly|annual)\b|' + CHANGE, q):
        name = 'revenue_by_year'
    elif re.search(r'\b(revenue|sales|total|sell|sold|turnover|money|made|make|brought in'
                   r'|bring in)\b', q):
        name = 'total_revenue'
    elif re.search(r'\b(do|did|does|perform\w*|go|went|look)\b', q) and (
            'year' in scope or 'store' in scope or 'channel' in scope):
        name = 'total_revenue'  # "How did 2027 do?"
    elif question_flags(question)['decision'] and 'store' in scope:
        name = 'revenue_by_year'  # "should we close Chicago?" -> show Chicago's trend
    else:
        return None
    allowed = TOOLS[name]['params']
    return name, {k: v for k, v in scope.items() if k in allowed}


# --------------------------------------------------------- language-model router
def tool_declarations(df) -> list:
    """Function declarations for the fixed TOOLS, in JSON Schema. The allowed channel
    values and the listed years come from the loaded data, not from hardcoded lists."""
    channels = sorted(df['StoreType'].dropna().unique()) if 'StoreType' in df else []
    years = sorted(int(y) for y in df['Year'].dropna().unique()) if 'Year' in df else []
    props = {
        'year': {'type': 'integer', 'description': f'Calendar year. Years in the data: {years}.'},
        'start_year': {'type': 'integer', 'description': f'First year to compare. Years: {years}.'},
        'end_year': {'type': 'integer', 'description': f'Last year to compare. Years: {years}.'},
        'channel': {'type': 'string', 'description': 'Sales channel (the store type).'},
        'store': {'type': 'string', 'description': 'One store: its ID (e.g. SH01) or its city.'},
        'n': {'type': 'integer', 'description': 'How many stores to list (default 5).'},
    }
    if channels:
        props['channel']['enum'] = channels
    return [{'name': name, 'description': spec['description'],
             'parameters_json_schema': {
                 'type': 'object',
                 'properties': {p: props[p] for p in sorted(spec['params'])},
                 'additionalProperties': False}}
            for name, spec in TOOLS.items()]


def _clean_args(args: dict) -> dict:
    """JSON numbers can arrive as floats (2029.0); turn whole numbers back into ints."""
    return {k: int(v) if isinstance(v, float) and v.is_integer() else v for k, v in args.items()}


def llm_router(question: str, df, client, model: str = GEMINI_MODEL):
    """Option A router: Gemini picks ONE tool from TOOLS through function calling.
    Mode VALIDATED uses constrained decoding, so a call can only name one of our tools
    with arguments that fit its schema. Gemini never sees the data. Returns
    (tool_name, arguments), or None if it declined. API errors are raised so that
    answer_question can fall back to the rules router."""
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=ROUTER_PROMPT,
        tools=[types.Tool(function_declarations=[
            types.FunctionDeclaration(**d) for d in tool_declarations(df)])],
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
            mode='VALIDATED', allowed_function_names=list(TOOLS))),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0,
    )
    response = client.models.generate_content(model=model, contents=question, config=config)
    calls = response.function_calls or []
    if not calls:
        return None
    return calls[0].name, _clean_args(dict(calls[0].args or {}))


def llm_explain(question: str, result: ToolResult, flags: dict, client,
                model: str = GEMINI_MODEL) -> dict:
    """Gemini words Findings, Interpretation and Next steps from the tool result only.
    Returns a dict with those three keys. API errors are raised to the caller."""
    from google.genai import types

    notes = []
    if flags['causal']:
        notes.append('The question asks about causes: say clearly that this is association only.')
    if flags['decision']:
        notes.append('The question asks for a decision: next_steps must be exactly '
                     f'"{NO_RECOMMENDATION}"')
    prompt = (f'Manager question: {question}\n\nTool result (JSON):\n'
              f'{json.dumps(result.to_dict(), default=str)}\n\n' + '\n'.join(notes))
    config = types.GenerateContentConfig(
        system_instruction=EXPLAIN_PROMPT, temperature=0,
        response_mime_type='application/json', response_json_schema=EXPLAIN_SCHEMA)
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    parsed = json.loads(response.text)
    return {k: str(parsed.get(k, '')).strip() for k in ('findings', 'interpretation', 'next_steps')}


# ------------------------------------------------------------ number guard
NUMBER = re.compile(r'(?<![A-Za-z0-9.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?')


def _numbers_in(obj) -> list:
    """Every number inside a tool result: values, numeric keys, and list lengths."""
    out = []
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, str):
        return [float(a.replace(',', '') + (b or '')) for a, b in NUMBER.findall(obj)]
    if isinstance(obj, dict):
        out.append(float(len(obj)))
        for k, v in obj.items():
            out += _numbers_in(k) + _numbers_in(v)
    if isinstance(obj, (list, tuple)):
        out.append(float(len(obj)))
        for v in obj:
            out += _numbers_in(v)
    return out


def unsupported_numbers(text: str, result: ToolResult, question: str = '',
                        years=()) -> list:
    """Numbers in `text` that cannot be traced to the tool result, the question, or a
    year in the loaded data. A written number matches a result value if it equals that
    value, or the value in thousands, millions or as a percentage, allowing for the
    rounding shown."""
    allowed = {float(y) for y in years}
    for v in _numbers_in(result.to_dict()) + _numbers_in(question):
        for scaled in (v, v / 1e3, v / 1e6, v * 100):
            allowed.add(abs(scaled))
    bad = []
    for whole, frac in NUMBER.findall(text):
        x = float(whole.replace(',', '') + (frac or ''))
        tolerance = 0.5 * 10 ** -(len(frac) - 1 if frac else 0)
        if not any(abs(x - a) <= tolerance for a in allowed):
            bad.append(whole + (frac or ''))
    return bad


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
    return (f"Scope: {scope.get('store', 'all stores')}, {year}, "
            f"{scope.get('channel', 'all channels')} ({FULL_DATA_NOTE})")


def _strength(r: float) -> str:
    size = abs(r)
    word = ('almost no' if size < 0.1 else 'a weak' if size < 0.3 else
            'a moderate' if size < 0.5 else 'a strong')
    return word + ('' if size < 0.1 else ' positive' if r > 0 else ' negative')


def findings_and_interpretation(result: ToolResult):
    """Template answer: Findings (only numbers from the result) and a one-sentence
    Interpretation computed from those same numbers. Used by the rules-based assistant
    and whenever Gemini's wording fails the number check."""
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
        best, worst = v['best_year'], v['worst_year']
        return (f"{years}. Best year: {best['year']} ({_money(best['revenue'])}); weakest: "
                f"{worst['year']} ({_money(worst['revenue'])}).",
                f'Revenue {word} {abs(change):.1f}% from {first} to {last}.')
    if result.tool == 'monthly_revenue_share':
        lines = '; '.join(f"{k}: {_money(x['revenue'])} ({_pct(x['share'])})"
                          for k, x in v['by_month'].items())
        best, worst = v['best_month'], v['worst_month']
        share = v['by_month'][best['month']]['share']
        return (lines, f"{best['month']} is the strongest month with {_pct(share)} of revenue in "
                       f"this scope; {worst['month']} is the weakest.")
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
    if result.tool == 'revenue_change_by_store':
        start, end = v['start_year'], v['end_year']

        def describe(r):
            return (f"{r['name']}: {_money(r['start_revenue'])} in {start} to "
                    f"{_money(r['end_revenue'])} in {end} ({r['change_pct']:+.1f}%)")
        parts = []
        if v['largest_decline']:
            parts.append('Largest decline: ' + describe(v['largest_decline']) + '.')
        else:
            parts.append(f'No store had lower revenue in {end} than in {start}.')
        if v['largest_growth']:
            parts.append('Largest growth: ' + describe(v['largest_growth']) + '.')
        return (' '.join(parts),
                f"{v['stores_declined']} of {v['stores_compared']} stores compared had lower "
                f'revenue in {end} than in {start}.')
    if result.tool == 'revenue_change_by_channel':
        start, end = v['start_year'], v['end_year']
        lines = '; '.join(f"{r['name']}: {_money(r['start_revenue'])} in {start} to "
                          f"{_money(r['end_revenue'])} in {end} ({r['change_pct']:+.1f}%)"
                          for r in v['channels'])
        best = v['largest_growth']
        if best and best['change'] > 0:
            return lines, f"{best['name']} grew the most between {start} and {end}."
        return lines, f'No channel grew between {start} and {end}.'
    if result.tool == 'satisfaction_vs_revenue':
        return (f"Correlation between store-year revenue and satisfaction: {v['correlation']} "
                f"across {v['store_years_used']} store-years ({v['store_years_without_score']} "
                f"without a score were left out). Average satisfaction: {v['average_satisfaction']}.",
                f"That is {_strength(v['correlation'])} association. It does not show that "
                'satisfaction causes revenue.')
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
    'monthly_revenue_share': 'Compare the monthly pattern for the Website and Brick & Mortar.',
    'revenue_by_category': 'Check whether the category mix differs between the Website and Brick & Mortar.',
    'revenue_by_channel': 'Look at revenue by year for each channel to see how the split has moved.',
    'top_stores': 'Ask for one of these stores by ID to see its categories or yearly trend.',
    'revenue_change_by_store': 'Look at the declining stores\' categories to see where their revenue fell.',
    'revenue_change_by_channel': 'Check which categories drove each channel\'s change.',
    'satisfaction_vs_revenue': 'Compare satisfaction within one market or store size, where stores are more alike.',
}


def answer_question(question: str, df, mode: str = 'rules', llm_client=None,
                    model: str = GEMINI_MODEL, survey=None) -> dict:
    """Route the question to one tool, run it through the dispatcher, and build the
    answer from the tool result. mode='llm' uses Gemini when a client is given: it picks
    the tool and words the explanation, and any Gemini failure falls back to the rules
    router or the template answer. Scope, caveats and limits always come from our code."""
    log: list = []
    answered_by, notes = 'Rules-based assistant', []
    use_llm = mode == 'llm' and llm_client is not None
    reason = rejection_reason(question)
    flags = question_flags(question)
    if reason:
        routed = None
    elif use_llm:
        try:
            routed, used = _with_backup(
                lambda m: llm_router(question, df, llm_client, m), model)
            answered_by = f'Gemini ({used}) chose the tool'
            log.append({'router': 'gemini', 'model': used,
                        'choice': {'tool': routed[0], 'arguments': routed[1]} if routed else None})
        except Exception as exc:  # any LLM failure falls back to the rules router
            logger.warning('Gemini router failed: %s', exc)
            notes.append(f'Gemini was unavailable ({type(exc).__name__}), so the rules-based '
                         'assistant answered instead.')
            use_llm = False
            routed = rules_router(question, df)
    else:
        routed = rules_router(question, df)
    base = {'answered_by': answered_by, 'tool_log': log}

    if routed is None:
        if reason:
            findings = f'I cannot answer that one. {reason} {CAN_ANSWER}'
        elif flags['causal']:
            findings = f'I cannot answer that one. {CAUSAL_CAVEAT} {CAN_ANSWER}'
        else:
            findings = UNSUPPORTED
        return {**base, 'note': ' '.join(notes), 'scope': describe_scope({}), 'findings': findings,
                'interpretation': '', 'caveats': [], 'next_steps': NO_RECOMMENDATION}
    name, args = routed
    result = run_tool(name, args, df, log, survey=survey)
    if result.error:
        return {**base, 'note': ' '.join(notes), 'scope': describe_scope(result.scope),
                'findings': f'The request could not be completed: {result.error}',
                'interpretation': '', 'caveats': [], 'next_steps': NO_RECOMMENDATION}

    findings, interpretation = findings_and_interpretation(result)
    next_steps = NEXT_STEPS.get(name, NO_RECOMMENDATION)
    if use_llm:
        try:
            text, used = _with_backup(
                lambda m: llm_explain(question, result, flags, llm_client, m), model)
            years = df['Year'].dropna().unique() if 'Year' in df else ()
            bad = unsupported_numbers(' '.join(text.values()), result, question, years)
            log.append({'explanation': 'gemini', 'model': used,
                        'numbers_not_in_tool_result': bad})
            if bad:
                notes.append(f"Gemini's wording included number(s) not in the tool result "
                             f"({', '.join(bad)}), so the standard answer is shown instead.")
            else:
                findings, interpretation, next_steps = (
                    text['findings'], text['interpretation'], text['next_steps'])
                answered_by += f' and {used} wrote the explanation'
        except Exception as exc:
            logger.warning('Gemini explanation failed: %s', exc)
            notes.append(f'Gemini could not write the explanation ({type(exc).__name__}), so '
                         'the standard answer is shown.')

    caveats = list(result.caveats)
    if flags['causal'] and CAUSAL_CAVEAT not in caveats:
        caveats.append(CAUSAL_CAVEAT)
    if flags['decision']:
        caveats.append(DECISION_GAPS)
        next_steps = f'{NO_RECOMMENDATION} {DECISION_GAPS}'
    return {
        **base,
        'answered_by': answered_by,
        'note': ' '.join(notes),
        'scope': describe_scope(result.scope),
        'findings': findings,
        'interpretation': interpretation,
        'caveats': caveats,
        'next_steps': next_steps,
    }
