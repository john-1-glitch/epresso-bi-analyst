"""Main app. Checkpoint 2: loads every E-Presso table, checks each join, and shows
total revenue. Keep calculations in analytics.py and the assistant in agent.py."""
import logging

import pandas as pd
import streamlit as st

import agent
import analytics

MAX_QUESTIONS = 15  # per-session limit for the assistant
FILE_TYPES = ['csv', 'txt', 'tsv', 'xlsx', 'xls']

logging.basicConfig(level=logging.INFO)  # merge checks are logged to the terminal

st.set_page_config(page_title='E-Presso BI Analyst', layout='wide')
st.title('E-Presso BI Analyst')
st.caption('Revenue analysis for E-Presso stores and the website, calculated from the files you upload.')


@st.cache_data(show_spinner=False)
def cached_dataset(uploads: tuple):
    # `uploads` holds each file's bytes, so the cache is keyed on the file CONTENTS:
    # a modified file (the $1,000 test) is recalculated, never served from the cache.
    return analytics.build_dataset(uploads)


def money(x: float) -> str:
    return f'${x:,.2f}'


with st.sidebar:
    st.header('Data')
    st.caption('Upload one file per table. For Excel files, the sheet with the right '
               'columns is found automatically.')
    uploaded = {}
    for role in analytics.REQUIRED_COLUMNS:
        label = analytics.TABLE_INFO[role][0]
        if role not in analytics.REQUIRED_TABLES:
            label += ' (optional)'
        file = st.file_uploader(label, type=FILE_TYPES, key=f'upload_{role}')
        if file is not None:
            uploaded[role] = file

waiting = [analytics.TABLE_INFO[r][0] for r in analytics.REQUIRED_TABLES if r not in uploaded]
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
    st.info('TODO: KPIs, filters, and at least three charts go here. '
            'Calculate everything from the loaded data. Never hardcode results.')

with tab_assistant:
    st.caption('Mode: rules-based starter. Describe what you actually built here.')
    st.session_state.setdefault('n_questions', 0)
    st.session_state.setdefault('history', [])
    for item in st.session_state['history']:
        with st.chat_message('user'):
            st.write(item['question'])
        with st.chat_message('assistant'):
            st.markdown(f"**Scope:** {item['answer']['scope']}")
            st.markdown(f"**Findings:** {item['answer']['findings']}")
            if item['answer']['interpretation']:
                st.markdown(f"**Interpretation:** {item['answer']['interpretation']}")
            with st.expander('Tool-call log'):
                st.json(item['answer']['tool_log'])
    question = st.chat_input('Ask a question about the loaded data')
    if question:
        if st.session_state['n_questions'] >= MAX_QUESTIONS:
            st.warning('Question limit reached for this session. Reload the page to start over.')
        else:
            st.session_state['n_questions'] += 1
            answer = agent.answer_question(question, model, mode='rules')
            st.session_state['history'].append({'question': question, 'answer': answer})
            st.rerun()
