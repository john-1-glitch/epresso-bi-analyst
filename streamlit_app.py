"""Main app: loads the E-Presso tables, checks each join, shows revenue, a filtered
dashboard and the analytical assistant. Calculations live in analytics.py and the
assistant in agent.py."""
import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import agent
import analytics

MAX_QUESTIONS = 15  # per-session limit for the assistant
FILE_TYPES = ['csv', 'txt', 'tsv', 'xlsx', 'xls', 'accdb', 'mdb']

logging.basicConfig(level=logging.INFO)  # merge checks are logged to the terminal

st.set_page_config(page_title='E-Presso BI Analyst', layout='wide')
st.title('E-Presso BI Analyst')
st.caption('Revenue analysis for E-Presso stores and the website, calculated from the files you upload.')


@st.cache_data(show_spinner=False)
def cached_dataset(uploads: tuple):
    # `uploads` holds each file's bytes, so the cache is keyed on the file CONTENTS:
    # a modified file (the $1,000 test) is recalculated, never served from the cache.
    return analytics.build_dataset(uploads)


def secret(name: str, default=None):
    """Read a Streamlit secret; returns the default when there is no secrets file."""
    try:
        return st.secrets.get(name, default)
    except Exception:  # no .streamlit/secrets.toml (local run without a key)
        return default


@st.cache_resource(show_spinner=False)
def gemini_client(api_key: str):
    from google import genai
    from google.genai import types
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=20_000))


def money(x: float) -> str:
    return f'${x:,.2f}'


# Categorical colors in a fixed order (colorblind-checked pair). Each channel keeps
# its color whatever the filters, because color follows the entity, not its rank.
SERIES_COLORS = ['#2a78d6', '#eb6834']
BAR_COLOR = SERIES_COLORS[0]


def style_chart(fig: go.Figure, title: str, height: int = 360) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=16)), height=height,
        margin=dict(l=8, r=8, t=56, b=8), hovermode='closest',
        legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='left', x=0),
        barcornerradius=4, bargap=0.35,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor='rgba(128,128,128,0.2)', zeroline=False)
    return fig


def table_view(df: pd.DataFrame, money_cols=('Revenue',), pct_cols=('Share',)):
    """Same numbers as the chart, as a table (for screen readers and exact values)."""
    out = df.copy()
    for col in money_cols:
        if col in out:
            out[col] = out[col].map(money)
    for col in pct_cols:
        if col in out:
            out[col] = out[col].map('{:.1%}'.format)
    with st.expander('View as table'):
        st.dataframe(out, hide_index=True)


with st.sidebar:
    st.header('Data')
    st.caption('Upload one file per table: CSV, Excel or Access. In a workbook or Access '
               'database, the sheet or table with the right columns is found automatically. '
               'If you upload the Access database (Support_EX_B_Sales.accdb) as Sales, '
               'Categories and Products are read from it too.')
    uploaded = {}
    for role in analytics.REQUIRED_COLUMNS:
        label = analytics.TABLE_INFO[role][0]
        if role not in analytics.REQUIRED_TABLES:
            label += ' (optional)'
        file = st.file_uploader(label, type=FILE_TYPES, key=f'upload_{role}')
        if file is not None:
            uploaded[role] = file

has_access = any(f.name.lower().endswith(analytics.ACCESS_EXT) for f in uploaded.values())
waiting = [analytics.TABLE_INFO[r][0] for r in analytics.REQUIRED_TABLES
           if r not in uploaded and not (has_access and r in ('categories', 'products'))]
if waiting:
    st.info('Upload these tables in the sidebar to begin: ' + ', '.join(waiting) + '.')
    st.stop()

data = cached_dataset(tuple((role, f.name, f.getvalue()) for role, f in uploaded.items()))
if data['errors']:
    for message in data['errors']:
        st.error(message)
    st.stop()

tables, model, reports = data['tables'], data['model'], data['merge_reports']
merges_ok = not any(r['changed'] for r in reports)


def render_dashboard():
    """Filters, KPIs and three charts. A function so that an early `return`
    (bad joins, empty selection) leaves the other tabs running."""
    if not merges_ok:
        st.error('A join changed the revenue total (see "Revenue and merge checks"). '
                 'The dashboard is hidden until the data is fixed.')
        return

    options = analytics.filter_options(model)
    channel_color = dict(zip(options['channels'], SERIES_COLORS * 4))

    # Filters: one row above the charts. Empty means "all".
    f1, f2, f3, f4 = st.columns(4)
    years = f1.multiselect('Year', options['years'], placeholder='All years', key='flt_year')
    channels = f2.multiselect('Channel', options['channels'], placeholder='All channels',
                              key='flt_channel')
    stores = f3.multiselect('Store', list(options['stores']), placeholder='All stores', key='flt_store',
                            format_func=options['stores'].get)
    categories = f4.multiselect('Category', options['categories'], placeholder='All categories',
                                key='flt_category')

    view = analytics.filter_sales(model, years, channels, stores, categories)
    scope = ' · '.join([
        ', '.join(map(str, years)) if years else 'All years',
        ', '.join(channels) if channels else 'all channels',
        f'{len(stores)} store(s)' if stores else 'all stores',
        ', '.join(categories) if categories else 'all categories',
    ])
    st.caption(f'Scope: {scope}')

    if view.empty:
        st.info('No sales match these filters. Remove a filter to see results.')
        return

    all_revenue = float(model['Revenue'].sum())
    view_revenue = float(view['Revenue'].sum())
    k1, k2, k3 = st.columns(3)
    k1.metric('Revenue in selection', money(view_revenue))
    k2.metric('Share of all revenue', f'{view_revenue / all_revenue:.1%}' if all_revenue else 'n/a')
    k3.metric('Stores in selection', view['Store ID'].nunique())
    st.caption('Revenue is not profit: the data has no costs, so margins cannot be shown.')

    # Chart 1: is revenue growing, and in which channel?
    by_year = analytics.revenue_by_year_channel(view)
    fig = go.Figure()
    for channel, part in by_year.groupby('StoreType'):
        fig.add_trace(go.Scatter(
            x=part['Year'], y=part['Revenue'], name=channel, mode='lines+markers',
            line=dict(width=2, color=channel_color[channel]), marker=dict(size=8),
            hovertemplate=f'<b>{channel}</b><br>%{{x}}: $%{{y:,.0f}}<extra></extra>'))
    fig.update_xaxes(dtick=1)
    fig.update_yaxes(tickprefix='$', tickformat='~s', rangemode='tozero')
    st.plotly_chart(style_chart(fig, 'Revenue by year and channel'), use_container_width=True)
    table_view(by_year.rename(columns={'StoreType': 'Channel'}))

    left, right = st.columns(2)

    # Chart 2: which product categories bring in the money?
    with left:
        by_cat = analytics.category_breakdown(view).iloc[::-1]  # largest at the top
        fig = go.Figure(go.Bar(
            x=by_cat['Revenue'], y=by_cat['Category'], orientation='h',
            marker_color=BAR_COLOR, customdata=by_cat['Share'],
            text=[f'${v / 1e6:,.1f}M · {s:.0%}' for v, s in zip(by_cat['Revenue'], by_cat['Share'])],
            textposition='outside', cliponaxis=False,
            hovertemplate='<b>%{y}</b><br>$%{x:,.0f} (%{customdata:.1%} of selection)<extra></extra>'))
        fig.update_xaxes(tickprefix='$', tickformat='~s', showgrid=True,
                         gridcolor='rgba(128,128,128,0.2)')
        st.plotly_chart(style_chart(fig, 'Revenue by category'), use_container_width=True)
        table_view(by_cat.iloc[::-1])

    # Chart 3: when in the year does revenue come in?
    with right:
        by_month = analytics.revenue_by_month(view)
        fig = go.Figure(go.Bar(
            x=by_month['Month'], y=by_month['Revenue'], marker_color=BAR_COLOR,
            customdata=by_month['Share'],
            hovertemplate='<b>%{x}</b><br>$%{y:,.0f} (%{customdata:.1%} of selection)<extra></extra>'))
        fig.update_yaxes(tickprefix='$', tickformat='~s')
        st.plotly_chart(style_chart(fig, 'Revenue by month (selected years combined)'),
                        use_container_width=True)
        table_view(by_month)


tab_data, tab_revenue, tab_dash, tab_assistant = st.tabs(
    ['Data and quality', 'Revenue and merge checks', 'Dashboard', 'Assistant'])

with tab_data:
    st.subheader('Grain statements')
    st.dataframe(analytics.grain_table(tables, data['sources']), hide_index=True)

    st.subheader('Data quality')
    issues = {k: v for k, v in data['sales_issues'].items() if v}
    if issues:
        for issue, count in issues.items():
            st.warning(f'Sales: {count:,} row(s) with {issue.lower()}.')
    else:
        st.success('Sales: no blank or invalid dates or revenue, no negative revenue, '
                   'no duplicate Sales IDs.')
    if 'survey' in tables:
        n_blank = int(tables['survey']['Satisfaction'].isna().sum())
        if n_blank:
            st.info(f'Survey Results: {n_blank} row(s) have no Satisfaction score (for example, '
                    'the website is not surveyed). They are left out of averages, not counted as zero.')

    role = st.selectbox('Profile a table', list(tables),
                        format_func=lambda r: analytics.TABLE_INFO[r][0])
    df = tables[role]
    profile = analytics.profile_table(df)
    c1, c2, c3 = st.columns(3)
    c1.metric('Rows', f"{profile['rows']:,}")
    c2.metric('Columns', profile['columns'])
    c3.metric('Duplicate rows', profile['duplicate_rows'])
    st.dataframe(df.head(50))
    left, right = st.columns(2)
    left.subheader('Missing values')
    left.dataframe(profile['missing'], hide_index=True)
    right.subheader('Data types')
    right.dataframe(profile['dtypes'], hide_index=True)

with tab_revenue:
    summary = analytics.revenue_summary(tables['sales'])
    c1, c2, c3 = st.columns(3)
    c1.metric('Total revenue', money(summary['total']))
    c2.metric('Sales rows used', f"{summary['rows_used']:,}")
    c3.metric('Rows excluded (blank revenue)', f"{summary['rows_excluded']:,}")

    st.subheader('Revenue by year')
    st.dataframe(pd.DataFrame({'Year': list(summary['by_year']),
                               'Revenue': [money(v) for v in summary['by_year'].values()]}),
                 hide_index=True)

    st.subheader('Merge checks')
    st.caption('Every lookup table is joined to Sales one at a time. Each join must leave '
               'the row count and the revenue total unchanged.')
    st.dataframe(pd.DataFrame([{
        'Join': r['merge'],
        'Rows before': f"{r['rows_before']:,}",
        'Rows after': f"{r['rows_after']:,}",
        'Revenue before': money(r['sum_before']),
        'Revenue after': money(r['sum_after']),
        'Unmatched rows': r['unmatched_rows'],
        'Status': 'Changed' if r['changed'] else 'OK',
    } for r in reports]), hide_index=True)
    for r in reports:
        if r['warning']:
            (st.error if r['changed'] else st.warning)(f"{r['merge']}: {r['warning']}")
    if merges_ok:
        st.success('All joins kept the same rows and the same revenue total.')

with tab_dash:
    render_dashboard()

with tab_assistant:
    api_key = secret('GEMINI_API_KEY')
    gemini_model = secret('GEMINI_MODEL', agent.GEMINI_MODEL)
    use_llm = bool(api_key) and st.toggle('Use Gemini (language-model agent)', value=True)
    if use_llm:
        st.caption(f'Mode: Gemini ({gemini_model}) agent. Gemini chooses ONE approved analytical '
                   'function through function calling, our code runs it on the loaded data, and '
                   'Gemini explains the result. Any number in its explanation that is not in '
                   'the tool result is caught and the standard answer is shown instead. If '
                   'Gemini fails or its quota runs out, the rules-based assistant answers.')
    else:
        st.caption('Mode: rules-based assistant. Keyword rules choose one approved analytical '
                   'function; the numbers come only from that function.'
                   + ('' if api_key else ' (No GEMINI_API_KEY in the app secrets.)'))
    with st.expander('What can I ask?'):
        st.markdown(agent.CAN_ANSWER + '\n\nExamples: "What was total revenue in 2029?", '
                    '"Website revenue by year", "Revenue by category for SH09", '
                    '"Top 3 stores in 2027", "Which store had the greatest percentage revenue '
                    'decline?", "Which sales channel grew the most?", "Does higher customer '
                    'satisfaction cause higher revenue?", "Should E-Presso close its Chicago '
                    'store?"')
    active = {name: st.session_state.get(f'flt_{key}') for key, name in
              (('year', 'Year'), ('channel', 'Channel'), ('store', 'Store'), ('category', 'Category'))}
    active = {k: v for k, v in active.items() if v}
    if active:
        st.info('Your dashboard filters (' + '; '.join(f"{k}: {', '.join(map(str, v))}"
                for k, v in active.items()) + ') do not apply here. The assistant answers from '
                'the full loaded dataset; name a year, channel or store in your question to '
                'narrow it.')
    st.session_state.setdefault('n_questions', 0)
    st.session_state.setdefault('history', [])
    st.caption(f"Questions used: {st.session_state['n_questions']} of {MAX_QUESTIONS}")
    for item in st.session_state['history']:
        answer = item['answer']
        with st.chat_message('user'):
            st.write(item['question'])
        with st.chat_message('assistant'):
            st.markdown(f"**{answer['scope']}**")
            st.markdown(f"**Findings**  \n{answer['findings']}")
            if answer['interpretation'] or answer['caveats']:
                st.markdown('**Interpretation**  \n' + answer['interpretation'] + ''.join(
                    f'\n- {c}' for c in answer['caveats']))
            st.markdown(f"**Next steps**  \n{answer['next_steps']}")
            if answer['note']:
                st.warning(answer['note'])
            st.caption(f"Answered by: {answer['answered_by']}")
            with st.expander('Tool-call log'):
                if answer['tool_log']:
                    st.json(answer['tool_log'])
                else:
                    st.write('No tool was called.')
    question = st.chat_input('Ask a question about the loaded data')
    if question:
        if st.session_state['n_questions'] >= MAX_QUESTIONS:
            st.warning('Question limit reached for this session. Reload the page to start over.')
        else:
            st.session_state['n_questions'] += 1
            if use_llm:
                try:
                    client = gemini_client(api_key)
                except Exception:  # SDK missing or bad key format: answer_question falls back
                    client = None
                answer = agent.answer_question(question, model, mode='llm', llm_client=client,
                                               model=gemini_model, survey=tables.get('survey'))
            else:
                answer = agent.answer_question(question, model, mode='rules',
                                               survey=tables.get('survey'))
            st.session_state['history'].append({'question': question, 'answer': answer})
            st.rerun()
