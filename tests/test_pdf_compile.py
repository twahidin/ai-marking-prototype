"""UP-31: PDF compile smoke. Requires a working LuaLaTeX install (see
CLAUDE.md "System Dependencies" — TeX Live + Noto fonts). Skipped on
machines without it so CI runs that lack a TeX install still pass.
"""

import shutil

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which('lualatex') is None,
    reason='lualatex not on PATH (install BasicTeX / TeX Live for the PDF smoke test)',
)


def test_minimal_report_compiles():
    """The smallest valid `result` dict that should still produce a PDF.
    If this goes red, it means we broke the LaTeX preamble or a callsite
    that the cache key memoises on. Either way it's a deploy-blocker."""
    from pdf_generator import generate_report_pdf

    result = {
        'questions': [
            {
                'question_num': 1,
                'feedback': 'Good attempt.',
                'status': 'correct',
                'student_answer': '42',
                'correct_answer': '42',
            }
        ],
        'overall_feedback': 'Solid first effort.',
    }
    pdf = generate_report_pdf(
        result,
        subject='General',
        app_title='AI Feedback Systems',
        assignment_name='Test Assignment',
    )
    assert isinstance(pdf, (bytes, bytearray))
    assert len(pdf) > 1000  # sanity floor: any real PDF is at least a few KB
    assert pdf.startswith(b'%PDF-')  # PDF magic bytes
