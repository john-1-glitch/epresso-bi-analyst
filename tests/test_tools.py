"""Tests for the assistant's tools, dispatcher and rules router, on the same six-row
dataset as test_epresso.py (see the hand-worked totals in that file's docstring).
Store A is in Lansing, store B in Denver, W is the website."""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import analytics  # noqa: E402
from test_epresso import STORES, uploads  # noqa: E402

CONTRACT_KEYS = {'tool', 'value', 'definition', 'scope', 'rows_used', 'caveats'}


@pytest.fixture
def model():
    return analytics.build_dataset(uploads())['model']


# ------------------------------------------------------------------ tools
def test_total_revenue_all_and_contract(model):
    r = analytics.total_revenue(model)
    assert r.value == {'revenue': 1000, 'share_of_all_revenue': 1.0}
    assert r.rows_used == 6
    assert set(r.to_dict()) == CONTRACT_KEYS  # exactly the tool contract
    assert r.scope == {'year': 'all years', 'store': 'all stores', 'channel': 'all channels'}
    assert 'Revenue is not profit' in r.caveats[0]


def test_total_revenue_with_year_and_channel(model):
    r = analytics.total_revenue(model, year=2026, channel='brick & mortar')  # any case
    assert r.value['revenue'] == 350 and r.rows_used == 2  # S4 200 + S5 150
    assert r.value['share_of_all_revenue'] == 0.35
    assert r.scope['channel'] == 'Brick & Mortar' and r.scope['year'] == 2026


def test_store_by_id_or_city(model):
    assert analytics.total_revenue(model, store='A').value['revenue'] == 250
    by_city = analytics.total_revenue(model, store='lansing')
    assert by_city.value['revenue'] == 250 and by_city.scope['store'] == 'A · Lansing (Midwest)'


def test_unknown_values_return_errors_not_guesses(model):
    for kwargs, text in [({'year': 2030}, 'Available years: [2025, 2026]'),
                         ({'channel': 'Phone'}, 'Valid channels'),
                         ({'store': 'Paris'}, 'Unknown store'),
                         ({'store': 'W', 'channel': 'Brick & Mortar'}, 'No sales match')]:
        r = analytics.total_revenue(model, **kwargs)
        assert r.value is None and r.rows_used == 0 and text in r.error
        assert 'error' in r.to_dict()


def test_city_with_two_stores_is_ambiguous():
    stores = STORES.copy()
    stores.loc[2, 'City'] = 'Lansing'  # B is now also in Lansing
    model = analytics.build_dataset(uploads(stores=stores))['model']
    r = analytics.total_revenue(model, store='Lansing')
    assert r.error and 'matches 2 stores' in r.error


def test_revenue_by_year(model):
    r = analytics.revenue_by_year(model)
    assert r.value['by_year'] == {'2025': 450, '2026': 550}
    assert r.value['change_first_to_last_pct'] == 22.22  # (550 - 450) / 450
    one_store = analytics.revenue_by_year(model, store='B')  # only 2026
    assert one_store.value == {'by_year': {'2026': 150}, 'change_first_to_last_pct': None}


def test_revenue_by_category_for_2025(model):
    r = analytics.revenue_by_category(model, year=2025)
    # 2025: Machines = S2 300, Beans = S1 100 + S3 50 = 150; total 450
    assert list(r.value['by_category']) == ['Machines', 'Beans']
    assert r.value['by_category']['Machines'] == {'revenue': 300, 'share': 0.6667}
    assert r.value['by_category']['Beans'] == {'revenue': 150, 'share': 0.3333}


def test_revenue_by_channel(model):
    r = analytics.revenue_by_channel(model)
    assert r.value['by_channel'] == {'Website': {'revenue': 600, 'share': 0.6},
                                     'Brick & Mortar': {'revenue': 400, 'share': 0.4}}


def test_top_stores(model):
    r = analytics.top_stores(model, n=2)
    assert [(s['store'], s['revenue']) for s in r.value['stores']] == [
        ('W · Website', 600), ('A · Lansing (Midwest)', 250)]
    assert r.value['stores_in_scope'] == 3
    assert analytics.top_stores(model, n=10).error  # only 3 stores exist


def test_tools_never_change_the_data(model):
    before = model.copy()
    analytics.total_revenue(model, year=2025)
    analytics.revenue_by_year(model)
    analytics.revenue_by_category(model, channel='Website')
    analytics.revenue_by_channel(model)
    analytics.top_stores(model)
    pd.testing.assert_frame_equal(model, before)


# ------------------------------------------------------------- dispatcher
def test_dispatcher_runs_and_logs_every_call(model):
    log = []
    r = agent.run_tool('total_revenue', {'year': 2025}, model, log)
    assert r.value['revenue'] == 450
    assert log[0]['tool'] == 'total_revenue' and log[0]['arguments'] == {'year': 2025}
    assert log[0]['result']['value']['revenue'] == 450
    agent.run_tool('delete_everything', {}, model, log)
    agent.run_tool('revenue_by_year', {'year': 2025}, model, log)  # not an allowed argument
    assert 'Unknown tool' in log[1]['result']['error']
    assert 'Unexpected argument' in log[2]['result']['error']


# ----------------------------------------------------------------- router
@pytest.mark.parametrize('question, expected', [
    ('What was total revenue in 2026?', ('total_revenue', {'year': 2026})),
    ('Website revenue in 2025', ('total_revenue', {'year': 2025, 'channel': 'Website'})),
    ('Revenue by category for Lansing', ('revenue_by_category', {'store': 'Lansing'})),
    ('Show me the top 2 stores', ('top_stores', {'n': 2})),
    ('How has website revenue grown?', ('revenue_by_year', {'channel': 'Website'})),
    ('Website vs brick and mortar', ('revenue_by_channel', {})),
    ('Any missing values?', ('data_quality_report', {})),
    ('Are there blanks in the data?', ('data_quality_report', {})),
    ('How much money did we make in 2026?', ('total_revenue', {'year': 2026})),
    ('In 2026, which three locations brought in the most?', ('top_stores', {'year': 2026, 'n': 3})),
    ('What sells best in Denver?', ('revenue_by_category', {'store': 'Denver'})),
])
def test_rules_router(model, question, expected):
    assert agent.rules_router(question, model) == expected


@pytest.mark.parametrize('question, reason', [
    ('What is our profit in 2029?', 'profit'),
    ('Which store is the most profitable?', 'profit'),
    ('Forecast revenue for next year', 'Forecasting'),
])
def test_unsupported_questions_are_rejected_with_reason(model, question, reason):
    out = agent.answer_question(question, model)
    assert 'cannot answer' in out['findings'] and reason in out['findings']
    assert out['tool_log'] == []  # no tool was run
    assert out['next_steps'] == agent.NO_RECOMMENDATION


def test_answer_has_scope_findings_interpretation_next_steps(model):
    out = agent.answer_question('Website revenue in 2025', model)
    assert out['scope'] == ('Scope: all stores, year 2025, Website (full loaded dataset; '
                            'dashboard filters are not applied)')
    assert out['findings'] == 'Revenue: $400.00 from 2 sales rows.'  # S1 100 + S2 300
    assert out['interpretation'] == 'That is 40.0% of all revenue in the loaded data.'
    assert out['next_steps'] and len(out['tool_log']) == 1


def test_bad_value_in_question_gives_clean_error(model):
    out = agent.answer_question('Total revenue in 2031', model)
    assert 'There are no sales for 2031' in out['findings']


# ------------------------------------------- change, satisfaction and judgment
# Store-year revenue in the six-row data: W 2025 = 400, 2026 = 200; A 2025 = 50,
# 2026 = 200; B only 2026 = 150. Channels: Website 400 -> 200, Brick & Mortar 50 -> 350.
SURVEY = analytics.clean_survey(pd.DataFrame({
    'Survey ID': ['A_25', 'A_26', 'B_26', 'W_25', 'W_26'],
    'Store ID': ['A', 'A', 'B', 'W', 'W'],
    'Year': ['Y2025', 'Y2026', 'Y2026', 'Y2025', 'Y2026'],
    'Satisfaction': [4, 3, 5, None, None],
}))


def test_revenue_change_by_store(model):
    r = analytics.revenue_change_by_store(model)
    assert r.scope['year'] == '2025 vs 2026'
    assert r.value['largest_decline'] == {'name': 'W · Website', 'start_revenue': 400,
                                          'end_revenue': 200, 'change': -200, 'change_pct': -50.0}
    assert r.value['largest_growth']['name'] == 'A · Lansing (Midwest)'
    assert r.value['largest_growth']['change_pct'] == 300.0   # 50 -> 200
    assert r.value['stores_declined'] == 1 and r.value['stores_compared'] == 2
    assert any('1 store(s) without revenue in both years' in c for c in r.caveats)  # B


def test_no_store_declined_is_said_plainly(model):
    r = analytics.revenue_change_by_store(model, channel='Brick & Mortar')  # only A compared
    assert r.value['largest_decline'] is None and r.value['stores_declined'] == 0
    assert 'No store had lower revenue in 2026 than in 2025.' in r.caveats
    findings, _ = agent.findings_and_interpretation(r)
    assert findings.startswith('No store had lower revenue in 2026 than in 2025.')


def test_change_years_are_validated(model):
    assert 'must be before' in analytics.revenue_change_by_store(model, 2026, 2025).error
    assert 'no sales for 2030' in analytics.revenue_change_by_channel(model, 2025, 2030).error


def test_revenue_change_by_channel(model):
    r = analytics.revenue_change_by_channel(model)
    got = {c['name']: (c['start_revenue'], c['end_revenue'], c['change_pct']) for c in r.value['channels']}
    assert got == {'Website': (400, 200, -50.0), 'Brick & Mortar': (50, 350, 600.0)}
    assert r.value['largest_growth']['name'] == 'Brick & Mortar'


def test_satisfaction_vs_revenue(model):
    r = analytics.satisfaction_vs_revenue(model, SURVEY)
    # pairs (revenue, score): (50, 4), (200, 3), (150, 5); Pearson r = -50 / sqrt(11666.67 x 2)
    assert r.value['correlation'] == pytest.approx(-0.327, abs=0.001)
    assert r.value['store_years_used'] == 3 and r.value['store_years_without_score'] == 2
    assert r.value['average_satisfaction'] == 4.0
    assert any('not causation' in c for c in r.caveats)
    assert 'not loaded' in analytics.satisfaction_vs_revenue(model, None).error


@pytest.mark.parametrize('question, expected', [
    ('Which store had the greatest percentage revenue decline?', ('revenue_change_by_store', {})),
    ('Which sales channel experienced the greatest revenue growth?', ('revenue_change_by_channel', {})),
    ('Which channel grew most between 2025 and 2026?',
     ('revenue_change_by_channel', {'start_year': 2025, 'end_year': 2026})),
    ('Does higher customer satisfaction cause higher revenue?', ('satisfaction_vs_revenue', {})),
    ('Should E-Presso close its Denver store?', ('revenue_by_year', {'store': 'Denver'})),
])
def test_rules_router_for_comparisons_and_judgment(model, question, expected):
    assert agent.rules_router(question, model) == expected


def test_causal_question_shows_evidence_with_limit(model):
    out = agent.answer_question('Does higher customer satisfaction cause higher revenue?',
                                model, survey=SURVEY)
    assert 'Correlation between store-year revenue and satisfaction: -0.327' in out['findings']
    assert agent.CAUSAL_CAVEAT in out['caveats']
    assert 'does not show that satisfaction causes revenue' in out['interpretation']


def test_decision_question_gives_evidence_but_no_recommendation(model):
    out = agent.answer_question('Should E-Presso close its Denver store?', model)
    assert out['findings'] == '2026: $150.00'
    assert out['next_steps'] == f'{agent.NO_RECOMMENDATION} {agent.DECISION_GAPS}'


def test_access_database_upload_fills_three_tables():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'raw',
                        'Support_EX_B_Sales.accdb')
    if not os.path.exists(path):
        pytest.skip('course Access file not present (it is git-ignored)')
    stores = os.path.join(os.path.dirname(path), 'Support_EX_B_Stores.csv')
    data = analytics.build_dataset([('sales', 'Sales.accdb', open(path, 'rb').read()),
                                    ('stores', 'Stores.csv', open(stores, 'rb').read())])
    assert data['errors'] == []
    assert set(data['tables']) == {'sales', 'stores', 'categories', 'products'}
    assert len(data['model']) == 25200
