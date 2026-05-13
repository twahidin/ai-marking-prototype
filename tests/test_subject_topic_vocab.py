"""UP-: subject topic vocabulary loading and per-subject lookup."""

from config.subject_topics import (
    get_topics_for_subject,
    is_known_topic_key,
    SUBJECTS_WITH_VOCAB,
)


def test_biology_topics_include_enzymes():
    topics = get_topics_for_subject('biology')
    keys = [k for k, _ in topics]
    assert 'enzymes' in keys
    assert 'terminology_precision' in keys


def test_unknown_subject_returns_empty_list():
    assert get_topics_for_subject('underwater_basketweaving') == []


def test_is_known_topic_key_positive():
    assert is_known_topic_key('biology', 'enzymes') is True


def test_is_known_topic_key_negative_unknown():
    assert is_known_topic_key('biology', 'flux_capacitor') is False


def test_subjects_with_vocab_lists_canonical_keys():
    assert 'biology' in SUBJECTS_WITH_VOCAB
    assert 'chemistry' in SUBJECTS_WITH_VOCAB
    assert 'mathematics' in SUBJECTS_WITH_VOCAB


def test_subject_topic_vocab_seeded_on_boot(app, db_session):
    from db import SubjectTopicVocabulary
    rows = SubjectTopicVocabulary.query.filter_by(subject='biology').all()
    keys = {r.topic_key for r in rows}
    assert 'enzymes' in keys
    assert 'terminology_precision' in keys
