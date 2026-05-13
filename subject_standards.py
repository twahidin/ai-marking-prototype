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
from ai_marking import extract_standard_topic_keys

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


def _text_similarity(a: str, b: str) -> float:
    """Cheap similarity score in [0, 1]. SequenceMatcher is enough at
    our scale - we're dedup'ing principles of ~50-250 chars, not search."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def find_similar_standard(subject: str, topic_keys: list, text: str):
    """Return an existing SubjectStandard with same subject, overlapping topic_keys,
    and text similarity >= SIMILARITY_THRESHOLD. Otherwise None."""
    if not topic_keys:
        return None
    candidates = SubjectStandard.query.filter_by(subject=subject).all()
    for c in candidates:
        c_keys = json.loads(c.topic_keys or '[]')
        if not any(k in c_keys for k in topic_keys):
            continue
        if _text_similarity(c.text, text) >= SIMILARITY_THRESHOLD:
            return c
    return None


def promote_to_subject_standard(feedback_edit_id, provider, model, session_keys):
    """Promote a FeedbackEdit to a SubjectStandard. Reinforces an existing
    near-duplicate or inserts a new pending_review row. Returns the
    SubjectStandard id."""
    from db import Assignment, Submission

    fe = FeedbackEdit.query.get(feedback_edit_id)
    if fe is None:
        raise ValueError(f'feedback_edit {feedback_edit_id} not found')

    asn = Assignment.query.get(fe.assignment_id)
    subject = (asn.subject or '').strip().lower() if asn else ''

    question_text = ''
    if fe.submission_id and fe.criterion_id:
        sub = Submission.query.get(fe.submission_id)
        if sub:
            result = sub.get_result() or {}
            for q in (result.get('questions') or []):
                if str(q.get('question_num')) == str(fe.criterion_id):
                    question_text = q.get('question', '') or ''
                    break

    topic_keys = extract_standard_topic_keys(
        provider=provider, model=model, session_keys=session_keys,
        subject=subject,
        question_text=question_text,
        original_feedback=fe.original_text or '',
        edited_feedback=fe.edited_text or '',
        theme_key=fe.theme_key,
    )

    existing = find_similar_standard(subject, topic_keys, fe.edited_text or '')
    if existing is not None:
        existing.reinforcement_count = (existing.reinforcement_count or 0) + 1
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.updated_at = datetime.now(timezone.utc)
        sources = json.loads(existing.source_feedback_edit_ids or '[]')
        if fe.id not in sources:
            sources.append(fe.id)
        existing.source_feedback_edit_ids = json.dumps(sources)
        fe.promoted_to_subject_standard_id = existing.id
        db.session.commit()
        return existing.id

    ss = SubjectStandard(
        subject=subject,
        text=fe.edited_text or '',
        topic_keys=json.dumps(topic_keys),
        theme_key=fe.theme_key,
        status='pending_review',
        created_by=fe.edited_by,
        source_feedback_edit_ids=json.dumps([fe.id]),
        reinforcement_count=1,
    )
    db.session.add(ss)
    db.session.flush()
    fe.promoted_to_subject_standard_id = ss.id
    db.session.commit()
    return ss.id


def retrieve_subject_standards(subject: str, per_question_topic_keys: list):
    """Pull active SubjectStandards for the assignment.

    Algorithm (see spec §4.5):
      1. For each question's topic_keys, query active standards whose
         topic_keys overlap.
      2. Per matched topic, take top `PER_TOPIC_QUOTA` by reinforcement_count.
      3. Dedup across questions/topics.
      4. Apply hard `ABSOLUTE_CAP`.

    Returns ordered list of SubjectStandard rows.
    """
    if not per_question_topic_keys:
        return []
    all_topic_keys = set()
    for tk_list in per_question_topic_keys:
        for k in (tk_list or []):
            all_topic_keys.add(k)
    if not all_topic_keys:
        return []

    candidates = (
        SubjectStandard.query
        .filter_by(subject=subject, status='active')
        .all()
    )

    seen_ids = set()
    selected = []
    # Track topics that still have quota room (fewer than PER_TOPIC_QUOTA matched).
    topics_with_room = set()
    for topic in all_topic_keys:
        topic_candidates = []
        for c in candidates:
            if c.id in seen_ids:
                continue
            ck = json.loads(c.topic_keys or '[]')
            if topic in ck:
                topic_candidates.append(c)
        topic_candidates.sort(key=lambda r: (-(r.reinforcement_count or 0), -(r.id or 0)))
        taken = topic_candidates[:PER_TOPIC_QUOTA]
        for c in taken:
            seen_ids.add(c.id)
            selected.append(c)
        if len(topic_candidates) < PER_TOPIC_QUOTA:
            topics_with_room.add(topic)

    # Second pass: add candidates that match only topics that didn't exhaust quota,
    # so we don't double-count overflow from topics already at their limit.
    remaining = []
    for c in candidates:
        if c.id in seen_ids:
            continue
        ck = json.loads(c.topic_keys or '[]')
        # Only include if it matches a topic that had room (wasn't quota-exhausted).
        if any(k in topics_with_room for k in ck):
            remaining.append(c)
    remaining.sort(key=lambda r: (-(r.reinforcement_count or 0), -(r.id or 0)))
    for c in remaining:
        if len(selected) >= ABSOLUTE_CAP:
            break
        selected.append(c)

    return selected[:ABSOLUTE_CAP]


def build_effective_answer_key(assignment, original_answer_key_text: str) -> str:
    """Return the original answer key text concatenated with a 'Teacher
    clarifications' section assembled from active amend_answer_key edits
    scoped to this assignment + rubric_version."""
    from ai_marking import _rubric_version_hash
    from db import Teacher

    rv = _rubric_version_hash(assignment)
    edits = (
        FeedbackEdit.query
        .filter_by(
            assignment_id=assignment.id,
            rubric_version=rv,
            active=True,
            amend_answer_key=True,
        )
        .order_by(FeedbackEdit.created_at.desc())
        .all()
    )
    if not edits:
        return original_answer_key_text or ''

    lines = [
        '',
        '── Teacher clarifications (added since upload) ──',
        '',
    ]
    for fe in edits:
        teacher = Teacher.query.get(fe.edited_by)
        name = teacher.name if teacher else 'teacher'
        date = fe.created_at.strftime('%Y-%m-%d') if fe.created_at else ''
        qn = fe.criterion_id
        lines.append(f"Q{qn}: {fe.edited_text}")
        lines.append(f"    Added by {name}, {date}.")
        lines.append('')

    return (original_answer_key_text or '') + '\n' + '\n'.join(lines)
