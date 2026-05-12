"""UP-41 — Bulk-marking routes blueprint (partial).

Hosts the `/bulk/*` routes (teacher-side multi-student PDF marking flow).
Depends on UP-15's persistent `BulkJob` model so a server restart no
longer drops in-flight bulk runs.

This is the FIRST WAVE of routes moved out of `app.py`. The main
endpoints (`bulk_mark`, `bulk_download`, `bulk_overview`) couple to
`_bulk_job_create`/`_load`/`_update`, the in-memory `jobs` dict,
`run_bulk_marking_job`, `_check_assignment_ownership`, and the
PDF/zipfile generators — extraction will follow when the bulk-job
helpers stabilise (they were last touched in UP-15).
"""

from flask import Blueprint, redirect, url_for

bp = Blueprint('bulk', __name__)


@bp.route('/bulk')
def bulk_page():
    """Legacy entry point — bulk marking is now mounted on the class page."""
    return redirect(url_for('class_page'))
