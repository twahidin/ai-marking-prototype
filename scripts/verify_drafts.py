"""Smoke tests for draft history. Run: python scripts/verify_drafts.py

Fails loudly on any broken invariant. Read-only — does not modify the database.
"""
import sys
from app import app
from db import db, Assignment, Submission, Student

FAIL = 0

def check(cond, msg):
    global FAIL
    if cond:
        print(f'PASS: {msg}')
    else:
        print(f'FAIL: {msg}')
        FAIL += 1

def main():
    with app.app_context():
        # Invariant 1: every (student, assignment) has at most one is_final=True
        rows = db.session.execute(
            db.text('SELECT student_id, assignment_id, COUNT(*) AS n FROM submissions WHERE is_final = TRUE GROUP BY student_id, assignment_id HAVING COUNT(*) > 1')
        ).fetchall()
        check(len(rows) == 0, f'At most one is_final per (student, assignment). Violations: {len(rows)}')

        # Invariant 2: if a student has any submissions, at least one is final
        no_final_rows = db.session.execute(
            db.text(
                'SELECT student_id, assignment_id FROM submissions '
                'GROUP BY student_id, assignment_id '
                'HAVING SUM(CASE WHEN is_final THEN 1 ELSE 0 END) = 0'
            )
        ).fetchall()
        check(len(no_final_rows) == 0, f'Every student with submissions has a final. Violations: {len(no_final_rows)}')

        # Invariant 3: draft_number is unique per (student, assignment)
        dup_rows = db.session.execute(
            db.text(
                'SELECT student_id, assignment_id, draft_number, COUNT(*) AS n FROM submissions '
                'GROUP BY student_id, assignment_id, draft_number HAVING COUNT(*) > 1'
            )
        ).fetchall()
        check(len(dup_rows) == 0, f'draft_number unique per (student, assignment). Violations: {len(dup_rows)}')

        # Invariant 4: draft_count <= max_drafts for drafts-enabled assignments
        violations = []
        for asn in Assignment.query.filter_by(allow_drafts=True).all():
            cap = asn.max_drafts or 3
            counts = db.session.execute(
                db.text('SELECT student_id, COUNT(*) AS n FROM submissions WHERE assignment_id = :aid GROUP BY student_id'),
                {'aid': asn.id}
            ).fetchall()
            for student_id, n in counts:
                if n > cap:
                    violations.append((asn.id, student_id, n, cap))
        check(len(violations) == 0, f'No student exceeds max_drafts. Violations: {violations}')

    if FAIL:
        print(f'\n{FAIL} checks failed')
        sys.exit(1)
    print('\nAll checks passed')

if __name__ == '__main__':
    main()
