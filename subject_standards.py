"""Per-assignment answer-key amendments from FeedbackEdit rows.

This module shrank dramatically in 2026-05-16: topic tagging, subject
standards retrieval, promotion, and dedup were all removed. The sole
remaining responsibility is assembling 'Teacher clarifications' from
active amend_answer_key=True edits into the marking prompt's effective
answer key.
"""
from db import FeedbackEdit


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
