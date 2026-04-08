import io
import re
import logging
from datetime import datetime, timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, black, white
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

logger = logging.getLogger(__name__)

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

    # Remove $$ and $ delimiters
    text = text.replace('$$', '')
    text = re.sub(r'\$([^$]+)\$', r'\1', text)
    text = text.replace('$', '')

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


def generate_report_pdf(result, subject=''):
    """
    Generate a PDF feedback report from marking results.

    Args:
        result: Dict from mark_script() with questions, overall_feedback, etc.
        subject: Subject name for the header

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
    story.append(Paragraph("AI Marking Report", styles['Title_Custom']))
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
        total_awarded = sum(q.get('marks_awarded', 0) for q in questions)
        total_possible = sum(q.get('marks_total', 0) for q in questions)
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

    # Per-question details
    story.append(Paragraph("Question-by-Question Feedback", styles['Heading_Custom']))
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
            marks_text = f" ({q['marks_awarded']}/{q.get('marks_total', '?')})"
        header_data = [[
            Paragraph(f"<b>Question {q_num}</b>", ParagraphStyle('QH', parent=styles['TableCell'], textColor=white)),
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

        student_ans = clean_for_pdf(q.get('student_answer', 'N/A'))
        correct_ans = clean_for_pdf(q.get('correct_answer', 'N/A'))
        feedback = clean_for_pdf(q.get('feedback', ''))
        improvement = clean_for_pdf(q.get('improvement', ''))

        detail_rows.append([
            Paragraph('<b>Student Answer</b>', bold_cell),
            Paragraph(student_ans, cell)
        ])
        detail_rows.append([
            Paragraph('<b>Correct Answer</b>', bold_cell),
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

    # Overall feedback
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Overall Feedback", styles['Heading_Custom']))

    overall = clean_for_pdf(result.get('overall_feedback', 'No overall feedback provided.'))
    story.append(Paragraph(overall, styles['Body_Custom']))
    story.append(Spacer(1, 15))

    # Recommended actions
    actions = result.get('recommended_actions', [])
    if actions:
        story.append(Paragraph("Recommended Actions", styles['Heading_Custom']))
        for i, action in enumerate(actions, 1):
            story.append(Paragraph(f"{i}. {clean_for_pdf(action)}", styles['Body_Custom']))
        story.append(Spacer(1, 15))

    # Footer
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Generated by AI Marking Demo", styles['Footer']))

    doc.build(story)
    return buffer.getvalue()
