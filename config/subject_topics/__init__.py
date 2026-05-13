"""Per-subject topic vocabulary registry.

Each subject has its own module (e.g. `biology.py`) defining a `TOPICS` list
of `(topic_key, display_name)` tuples. This package exposes lookup helpers
used by the AI tagger, the marking-time retrieval, and the standards page UI.

Adding a subject: create config/subject_topics/<subject>.py with TOPICS, then
add the module name to `_SUBJECT_MODULES` below. Subject keys MUST match the
canonical keys in subjects.py.
"""
import importlib
from typing import Tuple, List

_SUBJECT_MODULES = (
    'biology',
    'chemistry',
    'physics',
    'mathematics',
    'english',
    'lower_secondary_science',
    'history',
    'geography',
)


def _load_topics(subject_key: str) -> List[Tuple[str, str]]:
    try:
        mod = importlib.import_module(f'config.subject_topics.{subject_key}')
    except ModuleNotFoundError:
        return []
    return list(getattr(mod, 'TOPICS', []))


SUBJECTS_WITH_VOCAB = tuple(_SUBJECT_MODULES)


def get_topics_for_subject(subject_key: str) -> List[Tuple[str, str]]:
    return _load_topics(subject_key)


def is_known_topic_key(subject_key: str, topic_key: str) -> bool:
    return any(k == topic_key for k, _ in _load_topics(subject_key))


def get_display_name(subject_key: str, topic_key: str) -> str:
    for k, label in _load_topics(subject_key):
        if k == topic_key:
            return label
    return topic_key
