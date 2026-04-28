import io
import os
import re
import hashlib
import logging
import tempfile
from datetime import datetime, timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, black, white
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

logger = logging.getLogger(__name__)

# --- LaTeX → inline PNG rendering for PDF reports ---
# Teacher / AI feedback may contain LaTeX in $...$ or $$...$$ delimiters. We
# render each math segment to a small PNG via matplotlib.mathtext and embed it
# inline in ReportLab Paragraphs via <img src="..."/>. Plain text segments are
# XML-escaped and passed through unchanged. Matplotlib is lazy-imported so an
# environment without it falls back to the old Unicode-approximation path.

_MATH_TMPDIR = None
_MATH_CACHE = {}  # math_tex -> filesystem path of rendered PNG
_MATPLOTLIB_OK = None  # tri-state: None = unprobed, True/False = probed


def _matplotlib_available():
    global _MATPLOTLIB_OK
    if _MATPLOTLIB_OK is not None:
        return _MATPLOTLIB_OK
    try:
        import matplotlib  # noqa: F401
        matplotlib.use('Agg')
        from matplotlib import mathtext  # noqa: F401
        _MATPLOTLIB_OK = True
    except Exception as e:
        logger.warning(f"matplotlib unavailable, PDF math falls back to Unicode: {e}")
        _MATPLOTLIB_OK = False
    return _MATPLOTLIB_OK


def _ensure_math_tmpdir():
    global _MATH_TMPDIR
    if _MATH_TMPDIR is None or not os.path.isdir(_MATH_TMPDIR):
        _MATH_TMPDIR = tempfile.mkdtemp(prefix='aimark_math_')
    return _MATH_TMPDIR


def _render_math_to_png(math_tex, fontsize=11):
    """Render a single math snippet to a PNG and return its path. Caches per unique tex string."""
    cached = _MATH_CACHE.get(math_tex)
    if cached and os.path.isfile(cached):
        return cached
    if not _matplotlib_available():
        return None
    try:
        from matplotlib import mathtext
        tmpdir = _ensure_math_tmpdir()
        h = hashlib.sha1(math_tex.encode('utf-8')).hexdigest()[:16]
        path = os.path.join(tmpdir, f'eq_{h}.png')
        # math_to_image needs the text wrapped in $...$ (it strips its own delimiters)
        mathtext.math_to_image(f'${math_tex}$', path, dpi=200, prop=dict(size=fontsize))
        _MATH_CACHE[math_tex] = path
        return path
    except Exception as e:
        logger.warning(f"Failed to render LaTeX '{math_tex}': {e}")
        return None


def _split_text_and_math(text):
    """Yield (kind, content) tuples where kind is 'text' or 'math'.

    Handles $...$ and $$...$$ (TeX delimiters), plus \\(...\\) and \\[...\\]
    (MathJax-style delimiters). Unbalanced delimiters fall through as text
    so bad input never crashes the PDF.
    """
    import re as _re
    # Find every math span and its boundaries — process them in order.
    pattern = _re.compile(
        r'\$\$(.+?)\$\$'         # $$...$$
        r'|\$(.+?)\$'            # $...$
        r'|\\\((.+?)\\\)'        # \(...\)
        r'|\\\[(.+?)\\\]',       # \[...\]
        _re.DOTALL,
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            yield ('text', text[pos:m.start()])
        # Whichever group matched holds the math body.
        body = next(g for g in m.groups() if g is not None)
        yield ('math', body)
        pos = m.end()
    if pos < len(text):
        yield ('text', text[pos:])


# --- Math normalization helpers --------------------------------------------
# AI feedback is inconsistent: same response often mixes unicode superscripts
# ('ms⁻²'), caret notation ('t^3'), and proper LaTeX ('$\\frac{1}{2}$'). The
# helpers below reshape everything into LaTeX wrapped in $...$ so matplotlib
# mathtext can render it uniformly.

_SUPER_TO_CHAR = {
    '¹': '1', '²': '2', '³': '3',
    '⁰': '0', '⁴': '4', '⁵': '5', '⁶': '6',
    '⁷': '7', '⁸': '8', '⁹': '9',
    '⁺': '+', '⁻': '-',
}
_SUB_TO_CHAR = {
    '₀': '0', '₁': '1', '₂': '2', '₃': '3',
    '₄': '4', '₅': '5', '₆': '6', '₇': '7',
    '₈': '8', '₉': '9',
}
_SUPER_RE = re.compile('[' + ''.join(_SUPER_TO_CHAR.keys()) + ']+')
_SUB_RE = re.compile('[' + ''.join(_SUB_TO_CHAR.keys()) + ']+')

# Bare-math patterns. Conservative — these only match shapes that are
# unambiguously mathematical, so wrapping won't capture prose accidentally.
# Order matters: outer expressions (brackets, integrals) come first so the
# inner caret-expressions inside them don't get wrapped twice.
_BARE_MATH_PATTERNS = [
    re.compile(r'\\int\b[^\$\n]*?\\,?d[a-zA-Z]+'),                # \int ... dx
    re.compile(r'\[[^\]\$\n]+\]_\S+(?:\^\S+)?'),                  # [expr]_a^b
    re.compile(r'\\(?:frac|sqrt|sum)\{[^}]*\}(?:\{[^}]*\})?'),    # \frac{a}{b}, \sqrt{x}
    re.compile(r'(?<![a-zA-Z])[a-zA-Z]+\^\{[^}]+\}'),             # x^{2}, ms^{-2}
    re.compile(r'(?<![a-zA-Z])[a-zA-Z]+\^[-+]?\d+'),              # x^2, ms^-2
]


def _normalize_unicode_math(text):
    """Replace unicode super/subscript runs with LaTeX wrapped in $...$.
    'ms⁻²' becomes '$\\mathrm{ms}^{-2}$'; a stray '²' becomes '${}^{2}$'."""
    def _word_super(m):
        base, run = m.group(1), m.group(2)
        return '$\\mathrm{' + base + '}^{' + ''.join(_SUPER_TO_CHAR[c] for c in run) + '}$'
    text = re.sub(
        r'([A-Za-z]+)([' + ''.join(_SUPER_TO_CHAR.keys()) + r']+)',
        _word_super, text,
    )
    text = _SUPER_RE.sub(
        lambda m: '${}^{' + ''.join(_SUPER_TO_CHAR[c] for c in m.group(0)) + '}$',
        text,
    )
    text = _SUB_RE.sub(
        lambda m: '${}_{' + ''.join(_SUB_TO_CHAR[c] for c in m.group(0)) + '}$',
        text,
    )
    return text


def _wrap_bare_math(text):
    """Wrap bare-math substrings in $...$ so they route through matplotlib.
    Re-splits on $ between patterns so an outer wrap (e.g. [expr]_0^3)
    protects its inner caret expressions from double-wrapping."""
    for pat in _BARE_MATH_PATTERNS:
        parts = text.split('$')
        for i, part in enumerate(parts):
            if i % 2 == 1:
                continue  # already inside $...$
            parts[i] = pat.sub(lambda m: '$' + m.group(0) + '$', part)
        text = '$'.join(parts)
    return text


def _preprocess_math_for_pdf(text):
    """Coerce mixed math notations into LaTeX inside $...$ so the rendering
    pipeline produces consistent output. Idempotent."""
    if not text:
        return text
    text = _normalize_unicode_math(text)
    text = _wrap_bare_math(text)
    return text


def render_latex_for_pdf(text, fontsize=11, img_height=14):
    """Return ReportLab Paragraph markup with inline math rendered as PNGs.

    For fields known to contain LaTeX (feedback, improvement, overall_feedback,
    student_answer, correct_answer). Falls back to clean_for_pdf's Unicode
    approximation when matplotlib isn't available or a specific equation fails.
    """
    if not text:
        return ''
    text = str(text)
    # Normalize unicode superscripts and bare math into $...$ first so the
    # matplotlib pipeline catches everything that looks like math.
    text = _preprocess_math_for_pdf(text)
    # Fast path: no math delimiters at all → defer to Unicode helper.
    if '$' not in text and '\\(' not in text and '\\[' not in text:
        return clean_for_pdf(text)
    if not _matplotlib_available():
        return clean_for_pdf(text)

    out = []
    for kind, content in _split_text_and_math(text):
        if kind == 'text':
            # XML-escape for ReportLab; keep newlines as <br/>.
            safe = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            safe = safe.replace('\n', '<br/>')
            out.append(safe)
        else:
            png_path = _render_math_to_png(content, fontsize=fontsize)
            if png_path:
                # Embed as inline image. Height in points; ReportLab scales width proportionally.
                out.append(f'<img src="{png_path}" valign="middle" height="{img_height}"/>')
            else:
                # Render failed — fall back to the Unicode-approximation for just this snippet.
                out.append(clean_for_pdf(f'${content}$'))
    return ''.join(out)

# Colors
PRIMARY_COLOR = HexColor('#667eea')
SUCCESS_COLOR = HexColor('#28a745')
DANGER_COLOR = HexColor('#dc3545')
WARNING_COLOR = HexColor('#f0ad4e')
TEXT_COLOR = HexColor('#333333')
LIGHT_GRAY = HexColor('#f8f9fa')
BORDER_COLOR = HexColor('#dee2e6')

STATUS_COLORS = {
    'correct': SUCCESS_COLOR,
    'partially_correct': WARNING_COLOR,
    'incorrect': DANGER_COLOR,
}

STATUS_LABELS = {
    'correct': 'Correct',
    'partially_correct': 'Partially Correct',
    'incorrect': 'Incorrect',
}


def clean_for_pdf(text):
    """Convert LaTeX math to readable Unicode and XML-escape for ReportLab."""
    if not text:
        return ''
    text = str(text)

    # Strip math delimiters — keep the body so the Unicode replacements
    # below still get a chance to convert \frac, \times, etc.
    text = re.sub(r'\$\$(.+?)\$\$', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\$([^$]+)\$', r'\1', text)
    text = text.replace('$', '')
    text = re.sub(r'\\\((.+?)\\\)', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.+?)\\\]', r'\1', text, flags=re.DOTALL)

    # Common LaTeX -> Unicode
    replacements = {
        '\\frac': lambda m: f'({m.group(1)})/({m.group(2)})',
        '\\times': '\u00d7', '\\cdot': '\u00b7', '\\div': '\u00f7',
        '\\pm': '\u00b1', '\\leq': '\u2264', '\\le': '\u2264',
        '\\geq': '\u2265', '\\ge': '\u2265', '\\neq': '\u2260',
        '\\approx': '\u2248', '\\infty': '\u221e', '\\pi': '\u03c0',
        '\\theta': '\u03b8', '\\alpha': '\u03b1', '\\beta': '\u03b2',
        '\\gamma': '\u03b3', '\\delta': '\u03b4', '\\sigma': '\u03c3',
        '\\mu': '\u03bc', '\\therefore': '\u2234', '\\rightarrow': '\u2192',
        '\\to': '\u2192', '\\Rightarrow': '\u21d2', '\\implies': '\u21d2',
        '\\sum': '\u03a3', '\\int': '\u222b', '\\sqrt': '\u221a',
        '\\degree': '\u00b0', '\\circ': '\u00b0', '\\angle': '\u2220',
        '\\triangle': '\u25b3', '\\perp': '\u22a5',
    }

    # Handle \\frac{a}{b} specially
    text = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'(\1)/(\2)', text)
    text = re.sub(r'\\sqrt\{([^}]*)\}', '\u221a(\\1)', text)

    for cmd, repl in replacements.items():
        if not callable(repl):
            text = text.replace(cmd, repl)

    # Remove remaining backslash commands
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    # Remove curly braces
    text = text.replace('{', '').replace('}', '')

    # XML-escape for ReportLab
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    return text


def get_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name='Title_Custom', parent=styles['Title'],
        fontSize=22, textColor=PRIMARY_COLOR, spaceAfter=20,
        alignment=TA_CENTER, fontName='Helvetica-Bold'
    ))
    styles.add(ParagraphStyle(
        name='Heading_Custom', parent=styles['Heading1'],
        fontSize=14, textColor=PRIMARY_COLOR, spaceBefore=15,
        spaceAfter=10, fontName='Helvetica-Bold'
    ))
    styles.add(ParagraphStyle(
        name='Body_Custom', parent=styles['Normal'],
        fontSize=10, textColor=TEXT_COLOR, alignment=TA_JUSTIFY,
        spaceAfter=6, leading=14
    ))
    styles.add(ParagraphStyle(
        name='TableCell', parent=styles['Normal'],
        fontSize=9, textColor=TEXT_COLOR, leading=12
    ))
    styles.add(ParagraphStyle(
        name='TableHeader', parent=styles['Normal'],
        fontSize=9, textColor=white, fontName='Helvetica-Bold',
        alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        name='Footer', parent=styles['Normal'],
        fontSize=8, textColor=HexColor('#888888'), alignment=TA_CENTER
    ))

    return styles


def generate_report_pdf(result, subject='', app_title='AI Feedback Systems'):
    """
    Generate a PDF feedback report from marking results.

    Args:
        result: Dict from mark_script() with questions, overall_feedback, etc.
        subject: Subject name for the header
        app_title: Application title for the report header

    Returns:
        PDF content as bytes
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=1.5 * cm, leftMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm
    )

    styles = get_styles()
    story = []

    # Title
    report_title = f"{app_title} Report" if app_title else "AI Marking Report"
    story.append(Paragraph(report_title, styles['Title_Custom']))
    story.append(Spacer(1, 5))

    # Info box
    cell = styles['TableCell']
    bold_cell = ParagraphStyle('InfoBold', parent=cell, fontName='Helvetica-Bold')

    now = datetime.now(timezone.utc).strftime('%d %B %Y, %H:%M UTC')
    provider_label = result.get('provider_label', result.get('provider', 'AI'))

    info_data = [
        [Paragraph('Subject:', bold_cell), Paragraph(clean_for_pdf(subject or 'General'), cell),
         Paragraph('Date:', bold_cell), Paragraph(now, cell)],
        [Paragraph('AI Provider:', bold_cell), Paragraph(clean_for_pdf(provider_label), cell),
         Paragraph('', bold_cell), Paragraph('', cell)],
    ]

    info_table = Table(info_data, colWidths=[2.5 * cm, 5.5 * cm, 2.5 * cm, 5.5 * cm])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 15))

    # Summary bar
    questions = result.get('questions', [])
    has_marks = any(q.get('marks_awarded') is not None for q in questions)

    if has_marks:
        total_awarded = sum((q.get('marks_awarded') or 0) for q in questions)
        total_possible = sum((q.get('marks_total') or 0) for q in questions)
        pct = round(total_awarded / total_possible * 100) if total_possible > 0 else 0

        summary_data = [[
            Paragraph(f"<b>Score: {total_awarded} / {total_possible}</b>", styles['TableCell']),
            Paragraph(f"<b>{pct}%</b>", styles['TableCell']),
            Paragraph(f"<b>Questions: {len(questions)}</b>", styles['TableCell']),
        ]]
        summary_table = Table(summary_data, colWidths=[5.33 * cm, 5.33 * cm, 5.33 * cm])
        summary_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('PADDING', (0, 0), (-1, -1), 10),
            ('BOX', (0, 0), (-1, -1), 2, PRIMARY_COLOR),
            ('TEXTCOLOR', (0, 0), (-1, -1), PRIMARY_COLOR),
            ('BACKGROUND', (0, 0), (-1, -1), white),
        ]))
    else:
        counts = {'correct': 0, 'partially_correct': 0, 'incorrect': 0}
        for q in questions:
            status = q.get('status', 'incorrect')
            if status in counts:
                counts[status] += 1

        summary_data = [[
            Paragraph(f"<b>Correct: {counts['correct']}</b>", styles['TableCell']),
            Paragraph(f"<b>Partially Correct: {counts['partially_correct']}</b>", styles['TableCell']),
            Paragraph(f"<b>Incorrect: {counts['incorrect']}</b>", styles['TableCell']),
            Paragraph(f"<b>Total: {len(questions)}</b>", styles['TableCell']),
        ]]
        summary_table = Table(summary_data, colWidths=[4 * cm, 4 * cm, 4 * cm, 4 * cm])
        summary_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('PADDING', (0, 0), (-1, -1), 10),
            ('BOX', (0, 0), (-1, -1), 2, PRIMARY_COLOR),
            ('TEXTCOLOR', (0, 0), (0, 0), SUCCESS_COLOR),
            ('TEXTCOLOR', (1, 0), (1, 0), WARNING_COLOR),
            ('TEXTCOLOR', (2, 0), (2, 0), DANGER_COLOR),
            ('TEXTCOLOR', (3, 0), (3, 0), PRIMARY_COLOR),
            ('BACKGROUND', (0, 0), (-1, -1), white),
        ]))

    story.append(summary_table)
    story.append(Spacer(1, 20))

    # Per-question/criterion details
    is_rubrics = result.get('assign_type') == 'rubrics'
    section_title = "Rubric Criteria Feedback" if is_rubrics else "Question-by-Question Feedback"
    item_label = "Criterion" if is_rubrics else "Question"
    ans_label = "Assessment" if is_rubrics else "Student Answer"
    ref_label = "Band Descriptor" if is_rubrics else "Correct Answer"

    story.append(Paragraph(section_title, styles['Heading_Custom']))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))

    for q in questions:
        q_num = q.get('question_num', '?')
        status = q.get('status', 'incorrect')
        status_label = STATUS_LABELS.get(status, status)
        status_color = STATUS_COLORS.get(status, DANGER_COLOR)

        # Question header with status (and marks if available)
        marks_text = ''
        if q.get('marks_awarded') is not None:
            mt_val = q.get('marks_total')
            marks_text = f" ({q['marks_awarded']}/{mt_val if mt_val is not None else '?'})"
        # Use criterion_name if available (rubrics mode)
        criterion_name = q.get('criterion_name', '')
        band_info = q.get('band', '')
        if is_rubrics and criterion_name:
            header_left = f"<b>{clean_for_pdf(criterion_name)}</b>"
            if band_info:
                header_left += f" — {clean_for_pdf(band_info)}"
        else:
            header_left = f"<b>{item_label} {q_num}</b>"

        header_data = [[
            Paragraph(header_left, ParagraphStyle('QH', parent=styles['TableCell'], textColor=white)),
            Paragraph(f"<b>{status_label}{marks_text}</b>", ParagraphStyle('QS', parent=styles['TableCell'], textColor=white, alignment=TA_CENTER)),
        ]]
        header_table = Table(header_data, colWidths=[12 * cm, 4 * cm])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), PRIMARY_COLOR),
            ('BACKGROUND', (1, 0), (1, 0), status_color),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(header_table)

        # Details
        detail_rows = []

        student_ans = render_latex_for_pdf(q.get('student_answer', 'N/A'))
        correct_ans = render_latex_for_pdf(q.get('correct_answer', 'N/A'))
        feedback = render_latex_for_pdf(q.get('feedback', ''))
        improvement = render_latex_for_pdf(q.get('improvement', ''))

        detail_rows.append([
            Paragraph(f'<b>{ans_label}</b>', bold_cell),
            Paragraph(student_ans, cell)
        ])
        detail_rows.append([
            Paragraph(f'<b>{ref_label}</b>', bold_cell),
            Paragraph(correct_ans, cell)
        ])
        if feedback:
            detail_rows.append([
                Paragraph('<b>Feedback</b>', bold_cell),
                Paragraph(feedback, cell)
            ])
        if improvement:
            detail_rows.append([
                Paragraph('<b>Improvement</b>', bold_cell),
                Paragraph(improvement, cell)
            ])

        detail_table = Table(detail_rows, colWidths=[3.5 * cm, 12.5 * cm])
        detail_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
            ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(detail_table)
        story.append(Spacer(1, 12))

    # Line-by-line errors (rubrics mode)
    errors = result.get('errors', [])
    if errors:
        story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"Line-by-Line Errors ({len(errors)})", styles['Heading_Custom']))
        story.append(Spacer(1, 6))

        error_header = [[
            Paragraph('<b>Type</b>', styles['TableHeader']),
            Paragraph('<b>Location</b>', styles['TableHeader']),
            Paragraph('<b>Original</b>', styles['TableHeader']),
            Paragraph('<b>Correction</b>', styles['TableHeader']),
        ]]
        error_rows = error_header
        for err in errors:
            error_rows.append([
                Paragraph(clean_for_pdf(err.get('type', '')).upper(), cell),
                Paragraph(clean_for_pdf(err.get('location', '')), cell),
                Paragraph(clean_for_pdf(err.get('original', '')), cell),
                Paragraph(clean_for_pdf(err.get('correction', '')), cell),
            ])

        error_table = Table(error_rows, colWidths=[2.5 * cm, 3.5 * cm, 5 * cm, 5 * cm])
        error_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COLOR),
            ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
            ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(error_table)
        story.append(Spacer(1, 15))

    # Overall feedback
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Overall Feedback", styles['Heading_Custom']))

    overall = render_latex_for_pdf(result.get('overall_feedback', 'No overall feedback provided.'))
    story.append(Paragraph(overall, styles['Body_Custom']))
    story.append(Spacer(1, 15))

    # Recommended actions
    actions = result.get('recommended_actions', [])
    if actions:
        story.append(Paragraph("Recommended Actions", styles['Heading_Custom']))
        for i, action in enumerate(actions, 1):
            story.append(Paragraph(f"{i}. {render_latex_for_pdf(action)}", styles['Body_Custom']))
        story.append(Spacer(1, 15))

    # Footer
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Generated by AI Marking Demo", styles['Footer']))

    doc.build(story)
    return buffer.getvalue()


def generate_overview_pdf(student_results, subject='', app_title='AI Feedback Systems'):
    """
    Generate a class overview PDF with item analysis.

    Args:
        student_results: List of dicts with {name, index, result} where result
                        is the marking result dict (with questions, etc.)
        subject: Subject name for the header
        app_title: Application title for the report header

    Returns:
        PDF content as bytes
    """
    from statistics import mean, median, stdev

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=1.5 * cm, leftMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm
    )

    styles = get_styles()
    story = []

    # Title
    story.append(Paragraph(f"{app_title} — Class Overview &amp; Item Analysis", styles['Title_Custom']))
    story.append(Spacer(1, 5))

    cell = styles['TableCell']
    bold_cell = ParagraphStyle('InfoBold', parent=cell, fontName='Helvetica-Bold')

    now = datetime.now(timezone.utc).strftime('%d %B %Y, %H:%M UTC')
    info_data = [
        [Paragraph('Subject:', bold_cell), Paragraph(clean_for_pdf(subject or 'General'), cell),
         Paragraph('Date:', bold_cell), Paragraph(now, cell)],
        [Paragraph('Total Students:', bold_cell), Paragraph(str(len(student_results)), cell),
         Paragraph('', bold_cell), Paragraph('', cell)],
    ]
    info_table = Table(info_data, colWidths=[2.5 * cm, 5.5 * cm, 2.5 * cm, 5.5 * cm])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 20))

    # --- Collect scores ---
    valid_results = [sr for sr in student_results if sr.get('result') and not sr['result'].get('error')]
    scores = []  # (name, index, total_awarded, total_possible, pct)
    has_marks = False

    for sr in valid_results:
        questions = sr['result'].get('questions', [])
        if any(q.get('marks_awarded') is not None for q in questions):
            has_marks = True
            ta = sum((q.get('marks_awarded') or 0) for q in questions)
            tp = sum((q.get('marks_total') or 0) for q in questions)
            pct = round(ta / tp * 100, 1) if tp > 0 else 0
            scores.append({'name': sr.get('name', ''), 'index': sr.get('index', ''),
                           'awarded': ta, 'possible': tp, 'pct': pct})
        else:
            correct = sum(1 for q in questions if q.get('status') == 'correct')
            total = len(questions)
            pct = round(correct / total * 100, 1) if total > 0 else 0
            scores.append({'name': sr.get('name', ''), 'index': sr.get('index', ''),
                           'awarded': correct, 'possible': total, 'pct': pct})

    # --- Class Summary ---
    story.append(Paragraph("Class Summary", styles['Heading_Custom']))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))

    if scores:
        pcts = [s['pct'] for s in scores]
        avg_pct = round(mean(pcts), 1)
        med_pct = round(median(pcts), 1)
        high_pct = max(pcts)
        low_pct = min(pcts)
        std_pct = round(stdev(pcts), 1) if len(pcts) > 1 else 0
        pass_count = sum(1 for p in pcts if p >= 50)
        pass_rate = round(pass_count / len(pcts) * 100)

        stat_header = ['Mean', 'Median', 'Highest', 'Lowest', 'Std Dev', 'Pass Rate']
        stat_values = [f'{avg_pct}%', f'{med_pct}%', f'{high_pct}%', f'{low_pct}%',
                       f'{std_pct}%', f'{pass_rate}% ({pass_count}/{len(scores)})']

        stat_data = [
            [Paragraph(f'<b>{h}</b>', styles['TableHeader']) for h in stat_header],
            [Paragraph(v, ParagraphStyle('StatVal', parent=cell, alignment=TA_CENTER)) for v in stat_values],
        ]
        stat_table = Table(stat_data, colWidths=[2.67 * cm] * 6)
        stat_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COLOR),
            ('BACKGROUND', (0, 1), (-1, 1), white),
            ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(stat_table)
        story.append(Spacer(1, 10))

        # Score distribution bands
        bands = [('0-24%', 0, 24), ('25-49%', 25, 49), ('50-74%', 50, 74), ('75-100%', 75, 100)]
        band_counts = []
        for label, lo, hi in bands:
            count = sum(1 for p in pcts if lo <= p <= hi)
            band_counts.append(count)

        dist_data = [
            [Paragraph(f'<b>{b[0]}</b>', styles['TableHeader']) for b in bands],
            [Paragraph(str(c), ParagraphStyle('DistVal', parent=cell, alignment=TA_CENTER))
             for c in band_counts],
        ]
        story.append(Spacer(1, 5))
        story.append(Paragraph("Score Distribution", ParagraphStyle(
            'DistTitle', parent=styles['Body_Custom'], fontName='Helvetica-Bold', fontSize=11)))
        dist_table = Table(dist_data, colWidths=[4 * cm] * 4)
        dist_colors = [DANGER_COLOR, WARNING_COLOR, HexColor('#5cb85c'), SUCCESS_COLOR]
        dist_style = [
            ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('BACKGROUND', (0, 1), (-1, 1), white),
        ]
        for i, color in enumerate(dist_colors):
            dist_style.append(('BACKGROUND', (i, 0), (i, 0), color))
        dist_table.setStyle(TableStyle(dist_style))
        story.append(dist_table)
    else:
        story.append(Paragraph("No scored results available.", styles['Body_Custom']))

    story.append(Spacer(1, 20))

    # --- Item Analysis ---
    story.append(Paragraph("Item Analysis", styles['Heading_Custom']))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))

    # Collect per-question stats
    question_stats = {}  # q_num -> {correct, partial, incorrect, total, marks_sum, marks_max}
    for sr in valid_results:
        questions = sr['result'].get('questions', [])
        for q in questions:
            qn = q.get('question_num', '?')
            key = str(qn)
            if key not in question_stats:
                question_stats[key] = {
                    'num': qn,
                    'criterion_name': q.get('criterion_name', ''),
                    'correct': 0, 'partial': 0, 'incorrect': 0, 'total': 0,
                    'marks_sum': 0, 'marks_max': 0,
                }
            qs = question_stats[key]
            qs['total'] += 1
            status = q.get('status', 'incorrect')
            if status == 'correct':
                qs['correct'] += 1
            elif status == 'partially_correct':
                qs['partial'] += 1
            else:
                qs['incorrect'] += 1
            if q.get('marks_awarded') is not None:
                qs['marks_sum'] += (q.get('marks_awarded') or 0)
                qs['marks_max'] = max(qs['marks_max'], (q.get('marks_total') or 0))

    if question_stats:
        # Sort by question number
        sorted_qs = sorted(question_stats.values(), key=lambda x: (
            int(x['num']) if str(x['num']).isdigit() else 999, str(x['num'])))

        is_rubrics = any(qs['criterion_name'] for qs in sorted_qs)
        q_label = 'Criterion' if is_rubrics else 'Q#'

        if has_marks:
            header_row = [q_label, 'Correct', 'Partial', 'Incorrect', 'Avg Marks', 'Max', 'Difficulty']
            col_widths = [2.8 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm, 2.2 * cm, 1.5 * cm, 2.2 * cm]
        else:
            header_row = [q_label, 'Correct', 'Partial', 'Incorrect', 'Difficulty']
            col_widths = [3 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 3 * cm]

        item_data = [[Paragraph(f'<b>{h}</b>', styles['TableHeader']) for h in header_row]]

        center_cell = ParagraphStyle('CenterCell', parent=cell, alignment=TA_CENTER)

        for qs in sorted_qs:
            n = qs['total'] or 1
            pct_correct = round(qs['correct'] / n * 100)
            pct_partial = round(qs['partial'] / n * 100)
            pct_incorrect = round(qs['incorrect'] / n * 100)

            # Difficulty index: % who got it fully correct
            difficulty = pct_correct
            diff_label = 'Easy' if difficulty >= 70 else ('Moderate' if difficulty >= 40 else 'Hard')
            diff_color = SUCCESS_COLOR if difficulty >= 70 else (WARNING_COLOR if difficulty >= 40 else DANGER_COLOR)

            q_name = qs['criterion_name'] if is_rubrics and qs['criterion_name'] else str(qs['num'])

            row = [
                Paragraph(clean_for_pdf(q_name), cell),
                Paragraph(f'{qs["correct"]} ({pct_correct}%)', center_cell),
                Paragraph(f'{qs["partial"]} ({pct_partial}%)', center_cell),
                Paragraph(f'{qs["incorrect"]} ({pct_incorrect}%)', center_cell),
            ]
            if has_marks:
                avg_marks = round(qs['marks_sum'] / n, 1)
                row.append(Paragraph(str(avg_marks), center_cell))
                row.append(Paragraph(str(qs['marks_max']), center_cell))
            row.append(Paragraph(f'<b>{diff_label}</b> ({difficulty}%)',
                                 ParagraphStyle('DiffCell', parent=center_cell,
                                                textColor=diff_color, fontName='Helvetica-Bold')))
            item_data.append(row)

        item_table = Table(item_data, colWidths=col_widths)
        item_style = [
            ('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COLOR),
            ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]
        # Alternate row colors
        for i in range(1, len(item_data)):
            if i % 2 == 0:
                item_style.append(('BACKGROUND', (0, i), (-1, i), LIGHT_GRAY))
        item_table.setStyle(TableStyle(item_style))
        story.append(item_table)
        story.append(Spacer(1, 15))

        # Weak areas
        weak = [qs for qs in sorted_qs if (qs['correct'] / max(qs['total'], 1) * 100) < 40]
        if weak:
            story.append(Paragraph("Areas Needing Attention", styles['Heading_Custom']))
            story.append(Spacer(1, 5))
            for qs in weak:
                q_name = qs['criterion_name'] if is_rubrics and qs['criterion_name'] else f"Question {qs['num']}"
                pct = round(qs['correct'] / max(qs['total'], 1) * 100)
                story.append(Paragraph(
                    f"<b>{clean_for_pdf(q_name)}</b> — Only {pct}% of students answered correctly. "
                    f"({qs['incorrect']} incorrect, {qs['partial']} partially correct out of {qs['total']})",
                    styles['Body_Custom']))
            story.append(Spacer(1, 15))

    story.append(Spacer(1, 10))

    # --- Student Scores Table ---
    story.append(Paragraph("Individual Scores", styles['Heading_Custom']))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))

    if scores:
        sorted_scores = sorted(scores, key=lambda x: x['pct'], reverse=True)
        score_header = ['Rank', 'Index', 'Name', 'Score', '%']
        score_data = [[Paragraph(f'<b>{h}</b>', styles['TableHeader']) for h in score_header]]
        center_cell = ParagraphStyle('CenterCell2', parent=cell, alignment=TA_CENTER)

        for rank, s in enumerate(sorted_scores, 1):
            score_str = f"{s['awarded']}/{s['possible']}" if has_marks else f"{s['awarded']}/{s['possible']}"
            pct_color = SUCCESS_COLOR if s['pct'] >= 50 else DANGER_COLOR
            score_data.append([
                Paragraph(str(rank), center_cell),
                Paragraph(str(s['index']), cell),
                Paragraph(clean_for_pdf(s['name']), cell),
                Paragraph(score_str, center_cell),
                Paragraph(f"{s['pct']}%", ParagraphStyle('PctCell', parent=center_cell, textColor=pct_color)),
            ])

        score_table = Table(score_data, colWidths=[1.5 * cm, 2.5 * cm, 6 * cm, 3 * cm, 3 * cm])
        score_style = [
            ('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COLOR),
            ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]
        for i in range(1, len(score_data)):
            if i % 2 == 0:
                score_style.append(('BACKGROUND', (0, i), (-1, i), LIGHT_GRAY))
        score_table.setStyle(TableStyle(score_style))
        story.append(score_table)

    # Footer
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Generated by AI Marking Demo", styles['Footer']))

    doc.build(story)
    return buffer.getvalue()
