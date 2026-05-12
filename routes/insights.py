"""UP-40 — Teacher insights routes blueprint (partial).

Hosts the per-teacher dashboard layout endpoints and (over time) the
widget data endpoints (`/teacher/insights/widget/*`) and the department
insights endpoints (`/department/insights/*`).

This is the FIRST WAVE of routes moved out of `app.py`. The widget
endpoints depend on `_check_class_access_for_teacher`,
`_submission_percent`, and several blob-deferred query helpers that are
themselves due for an extraction (see UP-40 in `.claude/UPGRADE_PLAN.md`
for the broader plan including `insights_helpers.py`). Those move with
their widgets when the helper module lands.
"""

import json

from flask import Blueprint, jsonify, request

from db import db, TeacherDashboardLayout

bp = Blueprint('insights', __name__)


@bp.route('/teacher/insights/layout', methods=['GET'])
def teacher_insights_layout_get():
    """Return the saved dashboard layout for (current teacher, class_id).
    Empty list means no widgets yet — that's the expected first-load state."""
    from app import _check_class_access_for_teacher
    class_id = (request.args.get('class_id') or '').strip()
    if not class_id:
        return jsonify({'success': False, 'error': 'class_id required'}), 400
    teacher, err = _check_class_access_for_teacher(class_id)
    if err:
        return err
    row = TeacherDashboardLayout.query.filter_by(
        teacher_id=teacher.id, class_id=class_id
    ).first()
    layout = []
    if row and row.layout_json:
        try:
            parsed = json.loads(row.layout_json)
            if isinstance(parsed, list):
                layout = parsed
        except (json.JSONDecodeError, TypeError):
            layout = []
    return jsonify({'success': True, 'layout': layout})


@bp.route('/teacher/insights/layout', methods=['PUT'])
def teacher_insights_layout_put():
    """Upsert the dashboard layout for (current teacher, class_id)."""
    from app import _check_class_access_for_teacher
    data = request.get_json(silent=True) or {}
    class_id = (data.get('class_id') or '').strip()
    layout = data.get('layout')
    if not class_id:
        return jsonify({'success': False, 'error': 'class_id required'}), 400
    if not isinstance(layout, list):
        return jsonify({'success': False, 'error': 'layout must be a list'}), 400
    teacher, err = _check_class_access_for_teacher(class_id)
    if err:
        return err
    row = TeacherDashboardLayout.query.filter_by(
        teacher_id=teacher.id, class_id=class_id
    ).first()
    payload = json.dumps(layout)
    if row:
        row.layout_json = payload
    else:
        row = TeacherDashboardLayout(
            teacher_id=teacher.id, class_id=class_id, layout_json=payload
        )
        db.session.add(row)
    db.session.commit()
    return jsonify({'success': True})
