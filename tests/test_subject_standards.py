"""UP-: AI topic tagging + SubjectStandard retrieval / promotion."""

from unittest.mock import patch


def test_extract_assignment_topic_keys_returns_list_per_question(app):
    from ai_marking import extract_assignment_topic_keys
    import json
    fake_response = {
        'questions': [
            {'question_num': 1, 'topic_keys': ['enzymes', 'terminology_precision']},
            {'question_num': 2, 'topic_keys': ['cellular_respiration']},
        ]
    }
    with app.app_context():
        with patch('ai_marking._simple_completion', return_value=json.dumps(fake_response)):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[
                    {'question_num': 1, 'text': 'State one factor affecting enzyme activity.', 'answer_key': 'temperature, pH'},
                    {'question_num': 2, 'text': 'Explain ATP production.', 'answer_key': 'mitochondria, ATP synthase'},
                ],
            )
    assert result == [
        ['enzymes', 'terminology_precision'],
        ['cellular_respiration'],
    ]


def test_extract_assignment_topic_keys_filters_unknown_keys(app):
    from ai_marking import extract_assignment_topic_keys
    import json
    fake_response = {
        'questions': [
            {'question_num': 1, 'topic_keys': ['enzymes', 'flux_capacitor']},
        ]
    }
    with app.app_context():
        with patch('ai_marking._simple_completion', return_value=json.dumps(fake_response)):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[{'question_num': 1, 'text': 'x', 'answer_key': 'y'}],
            )
    assert result == [['enzymes']]


def test_extract_assignment_topic_keys_returns_empty_on_failure(app):
    from ai_marking import extract_assignment_topic_keys
    with app.app_context():
        with patch('ai_marking._simple_completion', side_effect=Exception('network')):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[{'question_num': 1, 'text': 'x', 'answer_key': 'y'}],
            )
    assert result == [[]]
