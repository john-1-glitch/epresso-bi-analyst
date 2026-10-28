"""Tests for the Gemini router with a FAKE client, so they run offline, cost nothing,
and give the same result every time. The fake returns whatever function call we tell
it to, which lets us check how our code handles good, bad and failed model output."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import analytics  # noqa: E402
from test_epresso import uploads  # noqa: E402


class FakeGemini:
    """Stands in for google.genai.Client: client.models.generate_content(...).
    The routing call (which has tools) returns `name`/`args` as a function call; the
    explanation call (JSON output) returns `explanation`, or fails if none is given."""

    def __init__(self, name=None, args=None, error=None, explanation=None, busy_model=None):
        self.name, self.args, self.error = name, args or {}, error
        self.busy_model = busy_model  # this model answers 429, like a used-up free quota
        self.explanation = explanation
        self.requests = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.requests.append({'model': model, 'contents': contents, 'config': config})
        if self.error:
            raise self.error
        if model == self.busy_model:
            err = RuntimeError('429 RESOURCE_EXHAUSTED')
            err.code = 429
            raise err
        if config.response_mime_type == 'application/json':
            if self.explanation is None:
                raise RuntimeError('no explanation configured')
            return SimpleNamespace(text=json.dumps(self.explanation))
        calls = [SimpleNamespace(name=self.name, args=self.args)] if self.name else None
        return SimpleNamespace(function_calls=calls)


@pytest.fixture
def model():
    return analytics.build_dataset(uploads())['model']


def ask(model, question, client):
    return agent.answer_question(question, model, mode='llm', llm_client=client)


def test_model_choice_is_run_by_our_dispatcher(model):
    fake = FakeGemini('total_revenue', {'year': 2025.0, 'channel': 'Website'})  # float year
    out = ask(model, 'How much did the website make in 2025?', fake)
    assert out['findings'] == 'Revenue: $400.00 from 2 sales rows.'  # S1 100 + S2 300
    assert out['scope'].startswith('Scope: all stores, year 2025, Website')
    assert out['answered_by'].startswith('Gemini')
    router, call = out['tool_log']
    assert router['choice'] == {'tool': 'total_revenue', 'arguments': {'year': 2025, 'channel': 'Website'}}
    assert call['result']['value']['revenue'] == 400


def test_request_offers_only_our_tools_with_data_driven_channels(model):
    fake = FakeGemini('revenue_by_channel', {})
    ask(model, 'Website vs shops?', fake)
    config = fake.requests[0]['config']
    declared = {f.name for f in config.tools[0].function_declarations}
    assert declared == set(agent.TOOLS)
    assert config.tool_config.function_calling_config.allowed_function_names == list(agent.TOOLS)
    total = next(f for f in config.tools[0].function_declarations if f.name == 'total_revenue')
    assert total.parameters_json_schema['properties']['channel']['enum'] == ['Brick & Mortar', 'Website']
    assert total.parameters_json_schema['additionalProperties'] is False


def test_api_failure_falls_back_to_rules(model):
    fake = FakeGemini(error=RuntimeError('quota exceeded'))
    out = ask(model, 'What was total revenue in 2026?', fake)
    assert out['findings'] == 'Revenue: $550.00 from 3 sales rows.'
    assert out['answered_by'] == 'Rules-based assistant'
    assert 'Gemini was unavailable (RuntimeError)' in out['note']


def test_no_client_uses_rules(model):
    out = ask(model, 'What was total revenue in 2026?', None)
    assert out['answered_by'] == 'Rules-based assistant' and out['note'] == ''


def test_model_declining_means_unsupported(model):
    out = ask(model, 'Write me a poem about coffee', FakeGemini())
    assert 'cannot answer' in out['findings'] and out['next_steps'] == agent.NO_RECOMMENDATION


def test_profit_question_never_reaches_the_model(model):
    fake = FakeGemini('total_revenue', {})
    out = ask(model, 'What was our profit in 2026?', fake)
    assert fake.requests == [] and 'profit' in out['findings']


def test_unknown_tool_or_argument_from_model_is_rejected(model):
    out = ask(model, 'x', FakeGemini('run_python', {'code': 'import os'}))
    assert 'Unknown tool: run_python' in out['findings']
    out = ask(model, 'x', FakeGemini('revenue_by_year', {'year': 2025}))
    assert 'Unexpected argument' in out['findings']


def test_unknown_value_from_model_is_not_guessed(model):
    out = ask(model, 'Revenue in 2031?', FakeGemini('total_revenue', {'year': 2031}))
    assert 'There are no sales for 2031' in out['findings']


# ------------------------------------------------- Gemini-written explanations
GOOD = {'findings': 'The website made $400 in 2025, 40% of all revenue.',
        'interpretation': 'Most 2025 revenue in scope came from the website.',
        'next_steps': 'Compare 2025 with 2026 for the website.'}


def test_gemini_explanation_is_used_when_its_numbers_check_out(model):
    fake = FakeGemini('total_revenue', {'year': 2025, 'channel': 'Website'}, explanation=GOOD)
    out = ask(model, 'Website revenue in 2025?', fake)
    assert out['findings'] == GOOD['findings'] and out['note'] == ''
    assert out['answered_by'].endswith('wrote the explanation')
    assert out['tool_log'][-1] == {'explanation': 'gemini', 'model': agent.GEMINI_MODEL,
                                   'numbers_not_in_tool_result': []}
    assert 'Revenue is not profit' in out['caveats'][0]  # caveats still come from our code


def test_invented_number_in_explanation_falls_back_to_template(model):
    bad = dict(GOOD, findings='The website made $450 in 2025.')  # true value is $400
    fake = FakeGemini('total_revenue', {'year': 2025, 'channel': 'Website'}, explanation=bad)
    out = ask(model, 'Website revenue in 2025?', fake)
    assert out['findings'] == 'Revenue: $400.00 from 2 sales rows.'
    assert '450' in out['note']


def test_explanation_failure_keeps_gemini_routing_and_template_text(model):
    fake = FakeGemini('total_revenue', {'year': 2026})  # no explanation -> that call fails
    out = ask(model, 'Revenue in 2026?', fake)
    assert out['findings'] == 'Revenue: $550.00 from 3 sales rows.'
    assert 'could not write the explanation' in out['note']


def test_decision_question_never_gets_a_recommendation(model):
    pushy = dict(GOOD, next_steps='Close the store immediately.')
    fake = FakeGemini('revenue_by_year', {'store': 'A'}, explanation=dict(pushy, findings='Store A: $50 in 2025, $200 in 2026.'))
    out = ask(model, 'Should we close store A?', fake)
    assert out['next_steps'].startswith(agent.NO_RECOMMENDATION)
    assert 'operating expenses' in out['next_steps']


@pytest.mark.parametrize('text, bad', [
    ('Revenue was $400, 40% of the total, from 2 rows.', []),
    ('Revenue was $0.4K.', []),           # thousands
    ('Revenue was $450.', ['450']),
    ('Up 12.5% on last year.', ['12.5']),
])
def test_number_guard(model, text, bad):
    result = analytics.total_revenue(model, year=2025, channel='Website')
    assert agent.unsupported_numbers(text, result) == bad


def test_number_guard_accepts_millions_and_percent_rounding(model):
    result = analytics.ToolResult(tool='t', value={'revenue': 9181550.0, 'share': 0.6453},
                                  definition='', scope={}, rows_used=5040)
    assert agent.unsupported_numbers('$9.2M is 64.5% (5,040 rows); $9,181,550 total.', result) == []
    assert agent.unsupported_numbers('$9.5M', result) == ['9.5']


def test_used_up_quota_switches_to_the_backup_model(model):
    fake = FakeGemini('total_revenue', {'year': 2026}, busy_model=agent.GEMINI_MODEL,
                      explanation=dict(GOOD, findings='Revenue in 2026 was $550.'))
    out = ask(model, 'Revenue in 2026?', fake)
    assert out['answered_by'].startswith(f'Gemini ({agent.GEMINI_BACKUP_MODEL}) chose the tool')
    assert out['findings'] == 'Revenue in 2026 was $550.' and out['note'] == ''
    assert [r['model'] for r in fake.requests] == [agent.GEMINI_MODEL, agent.GEMINI_BACKUP_MODEL,
                                                   agent.GEMINI_MODEL, agent.GEMINI_BACKUP_MODEL]


def test_other_errors_do_not_retry_and_fall_back_to_rules(model):
    fake = FakeGemini(error=ValueError('bad key'))
    out = ask(model, 'Revenue in 2026?', fake)
    assert len(fake.requests) == 1 and out['answered_by'] == 'Rules-based assistant'
