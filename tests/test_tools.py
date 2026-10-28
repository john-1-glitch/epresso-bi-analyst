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
])
def test_rules_router(model, question, expected):
    assert agent.rules_router(question, model) == expected


@pytest.mark.parametrize('question, reason', [
    ('What is our profit in 2029?', 'profit'),
    ('Why did website sales grow?', 'not why'),
    ('Forecast revenue for next year', 'Forecasting'),
])
def test_unsupported_questions_are_rejected_with_reason(model, question, reason):
    out = agent.answer_question(question, model)
    assert 'cannot answer' in out['findings'] and reason in out['findings']
    assert out['tool_log'] == []  # no tool was run
    assert out['next_steps'] == agent.NO_RECOMMENDATION


def test_answer_has_scope_findings_interpretation_next_steps(model):
    out = agent.answer_question('Website revenue in 2025', model)
    assert out['scope'] == 'Scope: all stores, year 2025, Website'
    assert out['findings'] == 'Revenue: $400.00 from 2 sales rows.'  # S1 100 + S2 300
    assert out['interpretation'] == 'That is 40.0% of all revenue in the loaded data.'
    assert out['next_steps'] and len(out['tool_log']) == 1


def test_bad_value_in_question_gives_clean_error(model):
    out = agent.answer_question('Total revenue in 2031', model)
    assert 'There are no sales for 2031' in out['findings']
