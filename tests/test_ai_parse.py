"""UP-31: `parse_ai_response` smoke tests.

The parser is the single most-shared chokepoint between the three
providers and is full of fallback strategies that have grown by accretion
over time. These cases cover the shapes we've actually seen in
production logs:

  1. Clean JSON
  2. Smart quotes (Qwen)
  3. Markdown fences (OpenAI/Claude when ignoring instructions)
  4. <think> reasoning blocks (Qwen)
  5. Truncated JSON (rate-limit cutoff mid-stream)
  6. Empty / nonsense responses
"""

import pytest

from ai_marking import parse_ai_response


def test_clean_json_passthrough():
    out = parse_ai_response('{"questions": [{"q": 1, "feedback": "ok"}]}')
    assert out['questions'][0]['q'] == 1


def test_smart_quotes_are_replaced():
    """Qwen has been seen emitting U+201C / U+201D where regular " is expected."""
    raw = '{“questions”: [{“q”: 1}]}'
    out = parse_ai_response(raw)
    assert 'questions' in out


def test_markdown_fence_is_stripped():
    raw = '```json\n{"questions": [{"q": 2}]}\n```'
    out = parse_ai_response(raw)
    assert out['questions'][0]['q'] == 2


def test_qwen_think_block_is_dropped():
    raw = '<think>let me think about this</think>{"questions": [{"q": 3}]}'
    out = parse_ai_response(raw)
    assert out['questions'][0]['q'] == 3


def test_truncated_json_is_repaired():
    """Repair path closes a half-open object so we still return SOMETHING
    parseable. Result may be partial — that's expected — but it must not
    raise."""
    raw = '{"questions": [{"q": 4, "feedback": "good but not great'
    out = parse_ai_response(raw)
    assert isinstance(out, dict)
    # Either the repaired parse won (questions key present) OR we get the
    # explicit error sentinel — both are acceptable, an uncaught exception
    # is not.
    assert 'questions' in out or 'error' in out


def test_empty_response_returns_error():
    assert parse_ai_response('').get('error')
    assert parse_ai_response('   ').get('error')


def test_no_json_returns_error_with_raw():
    out = parse_ai_response('Sure, here is your answer: looks good!')
    assert out.get('error')
    assert out.get('raw')  # preserves original for the operator to inspect


@pytest.mark.parametrize('raw', [
    '{"questions": []}',
    '{"questions": [{"q": 1}], "overall": "fine"}',
])
def test_parametrized_valid_shapes(raw):
    out = parse_ai_response(raw)
    assert 'questions' in out
