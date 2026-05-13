"""Domain logic for SubjectStandard: vocab seeding, retrieval, promotion,
dedup. Imported by app.py and ai_marking.py; should not import from either
to avoid cycles.
"""
import json
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher

from config.subject_topics import get_topics_for_subject, SUBJECTS_WITH_VOCAB
from db import db, FeedbackEdit, SubjectStandard, SubjectTopicVocabulary

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.85
PER_TOPIC_QUOTA = 3
ABSOLUTE_CAP = 30


def seed_subject_topic_vocabulary():
    """Idempotent: inserts missing (subject, topic_key) rows from
    config/subject_topics/*. Existing rows are left untouched."""
    for subject_key in SUBJECTS_WITH_VOCAB:
        for topic_key, display in get_topics_for_subject(subject_key):
            exists = SubjectTopicVocabulary.query.filter_by(
                subject=subject_key,
                topic_key=topic_key,
            ).first()
            if exists is None:
                db.session.add(SubjectTopicVocabulary(
                    subject=subject_key,
                    topic_key=topic_key,
                    display_name=display,
                    active=True,
                ))
    db.session.commit()
