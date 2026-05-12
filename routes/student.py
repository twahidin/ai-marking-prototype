"""UP-39 — Student-facing routes blueprint (partial).

Hosts the `/submit/*` (student self-submission portal) and the
student-facing `/feedback/<asn>/<sub>/*` routes (read-only feedback view).

This is the FIRST WAVE of routes moved out of `app.py`. Heavier routes
that touch many app.py-internal helpers (upload + AI dispatch, verify
roster lookups, the feedback grouping page) remain on the monolith for
now and will follow in a subsequent migration — see the trailing block
in `app.py` for the still-resident routes.

Helpers shared with `app.py` (`_is_authenticated`, `is_demo_mode`,
`get_app_title`) are deferred-imported inside route bodies to avoid the
circular import at module load.
"""

import io

from flask import (
    Blueprint, jsonify, render_template, request, send_file, session,
)

from db import Assignment, Submission, Student
from pdf_generator import generate_report_pdf

bp = Blueprint('student', __name__)


@bp.route('/submit/<assignment_id>')
def student_page(assignment_id):
    from app import is_demo_mode
    asn = Assignment.query.get_or_404(assignment_id)
    return render_template(
        'submit.html',
        assignment_id=assignment_id,
        subject=asn.subject,
        demo_mode=is_demo_mode(),
    )


@bp.route('/submit/<assignment_id>/question-paper')
def student_question_paper(assignment_id):
    """Serve the assignment's question paper to a student who has verified the classroom code."""
    if not session.get(f'student_auth_{assignment_id}'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    asn = Assignment.query.get_or_404(assignment_id)
    if not asn.question_paper:
        return jsonify({'success': False, 'error': 'No question paper available'}), 404
    return send_file(
        io.BytesIO(asn.question_paper),
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f'{asn.classroom_code}_question_paper.pdf',
    )


@bp.route('/submit/<assignment_id>/review/<int:submission_id>')
def student_review_submission(assignment_id, submission_id):
    """Let a student review their previous submission results."""
    from app import _is_authenticated
    is_student = session.get(f'student_auth_{assignment_id}')
    is_teacher = _is_authenticated()
    if not is_student and not is_teacher:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id or sub.status != 'done':
        return jsonify({'success': False, 'error': 'Not found'}), 404

    asn = Assignment.query.get(assignment_id)
    if not asn or not asn.show_results:
        return jsonify({'success': False, 'error': 'Results are not available for this assignment'}), 403

    result = sub.get_result()
    return jsonify({'success': True, 'result': result})


@bp.route('/submit/<assignment_id>/status/<int:submission_id>')
def student_submission_status(assignment_id, submission_id):
    from app import _is_authenticated
    is_teacher = _is_authenticated()
    is_student = session.get(f'student_auth_{assignment_id}')
    if not is_student and not is_teacher:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404

    asn = Assignment.query.get(assignment_id)
    response = {'success': True, 'status': sub.status}

    if sub.status == 'preview':
        response['extracted'] = sub.get_extracted_text()

    if sub.status in ('done', 'error'):
        result = sub.get_result()
        # Teachers always see results; students only if show_results is on.
        if is_teacher or (asn and asn.show_results):
            response['result'] = result
        elif result.get('error'):
            response['result'] = {'error': result['error']}

    return jsonify(response)


@bp.route('/submit/<assignment_id>/download/<int:submission_id>')
def download_submission_pdf(assignment_id, submission_id):
    """Download a PDF report for a specific submission. Allowed for the
    assignment's teacher OR a student authenticated for this assignment;
    students additionally require asn.show_results=True so the teacher's
    'Issue AI Feedback' gate covers downloads as well as the in-browser
    view."""
    from app import _is_authenticated, get_app_title
    is_teacher = _is_authenticated()
    is_student = bool(session.get(f'student_auth_{assignment_id}'))
    if not is_teacher and not is_student:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    if sub.status != 'done':
        return jsonify({'success': False, 'error': 'No results available'}), 404

    asn = Assignment.query.get(assignment_id)
    if not is_teacher and not (asn and asn.show_results):
        return jsonify({'success': False, 'error': 'Feedback not yet released by the teacher'}), 403
    result = sub.get_result()
    subject = asn.subject if asn else ''
    asn_title = (asn.title if asn else '') or ''
    student = Student.query.get(sub.student_id) if sub.student_id else None
    student_name = (student.name if student else '') or ''
    pdf_bytes = generate_report_pdf(
        result, subject=subject, app_title=get_app_title(),
        assignment_name=asn_title,
        student_name=student_name,
    )

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name='AI_Marking_Report.pdf',
    )
