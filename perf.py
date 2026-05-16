"""Listing-page performance helpers.

Every route that renders a *list* of Assignments / Submissions / Students
should compose its query through these helpers instead of calling
`.query.all()` directly. They encapsulate the three patterns we have
had to retrofit repeatedly across the codebase:

1. **Defer heavy columns on listings.** `Assignment` carries four
   `LargeBinary` files (question paper / answer key / rubrics /
   reference) and `Submission` carries `script_bytes` plus several
   large JSON text columns. A bare `Assignment.query.all()` pulls
   *all* of them — often many MB per row — even when the page only
   reads `id`, `title`, `subject`, etc.
2. **Batch relationship counts into one `GROUP BY` query.** Jinja's
   `{{ asn.submissions | length }}` triggers a per-row lazy-load that
   itself drags the deferred-elsewhere blobs back into memory.
3. **(Caller's job.)** Inline first-paint data into the template
   context instead of an after-paint XHR. There is no helper for this
   — just remember to pass dropdown/list payloads as render context
   when the data is cheap enough.

See `CLAUDE.md` -> "Page-load performance" for the full checklist.
"""

from sqlalchemy import func
from sqlalchemy.orm import defer

from db import db, Assignment, Submission, Student


# Columns that almost never feature on a listing page. Deferring them
# keeps the SELECT small. If a caller actually touches one of these,
# SQLAlchemy lazy-loads it on first access — safe.
_ASSIGNMENT_HEAVY = (
    Assignment.question_paper,
    Assignment.answer_key,
    Assignment.rubrics,
    Assignment.reference,
    Assignment.exemplar_analysis_json,
    Assignment.api_keys_json,
    Assignment.review_instructions,
    Assignment.marking_instructions,
)

# For Submission, defer the script bytes + the bulky JSON text columns.
# `result_json` is intentionally NOT deferred: most listing pages read
# it to compute averages or status, and it's typically <100 KB.
_SUBMISSION_HEAVY = (
    Submission.script_bytes,
    Submission.script_pages_json,
    Submission.extracted_text_json,
    Submission.student_text_json,
)


def light_assignment_query():
    """`Assignment.query` with the heavy cols deferred. Use for ANY
    route that lists assignments."""
    return Assignment.query.options(*(defer(c) for c in _ASSIGNMENT_HEAVY))


def light_submission_query():
    """`Submission.query` with script bytes + bulky JSON text deferred.
    `result_json` stays eager — listing pages typically read it."""
    return Submission.query.options(*(defer(c) for c in _SUBMISSION_HEAVY))


def submission_counts(assignment_ids):
    """Map assignment_id -> total submission count via one `GROUP BY`.

    Use this instead of `asn.submissions | length` in templates. Pass
    the precomputed dict into render context.
    """
    if not assignment_ids:
        return {}
    rows = (db.session.query(Submission.assignment_id,
                             func.count(Submission.id))
            .filter(Submission.assignment_id.in_(list(assignment_ids)))
            .group_by(Submission.assignment_id).all())
    return {aid: n for aid, n in rows}


def student_counts_for_assignments(assignments):
    """Map assignment_id -> roster size.

    Handles both shapes in one call:
    - Dept-mode rows: `Student.class_id` is set; count by class.
    - Legacy single-mode rows: `Student.assignment_id` is set; count
      by assignment directly.

    `assignments` may be any iterable of Assignment-like objects with
    `id` and `class_id` attributes.
    """
    assignments = list(assignments)
    if not assignments:
        return {}
    class_ids = list({a.class_id for a in assignments if a.class_id})
    legacy_ids = [a.id for a in assignments if not a.class_id]

    class_counts = {}
    if class_ids:
        rows = (db.session.query(Student.class_id, func.count(Student.id))
                .filter(Student.class_id.in_(class_ids))
                .group_by(Student.class_id).all())
        class_counts = {cid: n for cid, n in rows}

    legacy_counts = {}
    if legacy_ids:
        rows = (db.session.query(Student.assignment_id, func.count(Student.id))
                .filter(Student.assignment_id.in_(legacy_ids))
                .group_by(Student.assignment_id).all())
        legacy_counts = {aid: n for aid, n in rows}

    out = {}
    for a in assignments:
        if a.class_id:
            out[a.id] = class_counts.get(a.class_id, 0)
        else:
            out[a.id] = legacy_counts.get(a.id, 0)
    return out


def student_counts_for_classes(class_ids):
    """Map class_id -> roster size. Use for class-dropdown payloads."""
    class_ids = list(class_ids)
    if not class_ids:
        return {}
    rows = (db.session.query(Student.class_id, func.count(Student.id))
            .filter(Student.class_id.in_(class_ids))
            .group_by(Student.class_id).all())
    return {cid: n for cid, n in rows}
