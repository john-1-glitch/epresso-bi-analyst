"""Tests for the Gemini router with a FAKE client, so they run offline, cost nothing,
and give the same result every time. The fake returns whatever function call we tell
it to, which lets us check how our code handles good, bad and failed model output."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import analytics  # noqa: E402
from test_epresso import uploads  # noqa: E402


class FakeGemini:
    """Stands in for google.genai.Client: client.models.generate_content(...)."""

    def __init__(self, name=None, args=None, error=None):
        self.name, self.args, self.error = name, args or {}, error
        self.requests = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.requests.append({'model': model, 'contents': contents, 'config': config})
        if self.error:
            raise self.error
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
    assert out['scope'] == 'Scope: all stores, year 2025, Website'
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
