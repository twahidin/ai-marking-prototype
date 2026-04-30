"""
PDF generation for marking feedback and class overview reports.

This module replaces the previous ReportLab + matplotlib-mathtext pipeline
with a single HTML → PDF flow built on WeasyPrint and latex2mathml.

Why HTML → PDF here:
  - The browser already renders feedback HTML with MathJax/KaTeX. Producing
    PDFs from the same shape of HTML keeps the rendering surface unified;
    we don't maintain a parallel ReportLab template that drifts from the
    web view.
  - WeasyPrint reads native MathML, so latex2mathml gives us scalable
    vector math without a Node.js renderer or rasterised images.
  - Tamil and CJK come for free as long as Noto fonts are installed at
    the OS level — handled in `nixpacks.toml` for Railway.

Public API (kept identical to the old module so app.py is unchanged):
  - generate_report_pdf(result, subject='', app_title='AI Feedback Systems') -> bytes
  - generate_overview_pdf(student_results, subject='', app_title='AI Feedback Systems') -> bytes
"""
import io
import re
import html as _stdhtml
import logging
from datetime import datetime, timezone
from statistics import mean, median, stdev

logger = logging.getLogger(__name__)

# Lazy-import WeasyPrint and latex2mathml so a missing system dependency
# (libpango, fontconfig) yields a clear runtime error instead of a hard
# import-time crash that takes the whole app down.
try:
    import latex2mathml.converter as _l2mc
    _LATEX2MATHML_OK = True
except Exception as _e:
    _LATEX2MATHML_OK = False
    logger.warning(f"latex2mathml unavailable, math will fall back to <code>: {_e}")

_WEASY_HTML = None
_WEASY_ERR = None


def _get_weasy():
    """Resolve WeasyPrint's HTML class lazily so import errors surface only
    when a PDF is actually requested. Returns the HTML class or raises
    RuntimeError with a useful message."""
    global _WEASY_HTML, _WEASY_ERR
    if _WEASY_HTML is not None:
        return _WEASY_HTML
    if _WEASY_ERR is not None:
        raise RuntimeError(_WEASY_ERR)
    try:
        from weasyprint import HTML as _HTML
        _WEASY_HTML = _HTML
        return _WEASY_HTML
    except Exception as e:
        _WEASY_ERR = (
            'WeasyPrint failed to import: ' + str(e) +
            '. On Railway this means Pango / fontconfig are missing — '
            "make sure nixpacks.toml installs them. Locally on macOS, "
            "run: brew install pango"
        )
        raise RuntimeError(_WEASY_ERR)


# ---------------------------------------------------------------------------
# Math conversion
# ---------------------------------------------------------------------------

def _latex_to_mathml(latex, display=False):
    """Convert one LaTeX fragment to MathML. Falls back to <code>$...$</code>
    if the converter isn't available or trips on a malformed fragment."""
    delim = '$$' if display else '$'
    if not _LATEX2MATHML_OK:
        return f'<code>{delim}{_esc(latex)}{delim}</code>'
    try:
        return _l2mc.convert(latex, display='block' if display else 'inline')
    except Exception as e:
        logger.debug(f"latex2mathml conversion failed for {latex[:60]!r}: {e}")
        return f'<code>{delim}{_esc(latex)}{delim}</code>'


def preprocess_math(text):
    """Replace $$...$$ (display) and $...$ (inline) with MathML.

    Display math is matched first so the inline regex doesn't consume the
    $$ delimiters. Inline uses lookbehind / lookahead to skip the literal
    $$ pairs left over by the display pass."""
    if not text:
        return ''
    text = str(text)

    text = re.sub(
        r'\$\$(.+?)\$\$',
        lambda m: _latex_to_mathml(m.group(1).strip(), display=True),
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r'(?<!\$)\$(?!\$)((?:[^$\n]|\n)+?)(?<!\$)\$(?!\$)',
        lambda m: _latex_to_mathml(m.group(1).strip(), display=False),
        text,
    )
    return text


def _esc(s):
    """HTML-escape user / AI text. Always called BEFORE preprocess_math —
    the math substitution emits trusted MathML markup that must survive,
    so we escape first then run the math regex over the escaped string."""
    return _stdhtml.escape('' if s is None else str(s), quote=False)


def _esc_md(s):
    """Escape, run math substitution, and convert simple newlines to <br>.
    Used for body fields that may contain $...$ math from the AI output
    (feedback, improvement, student answers, overall feedback)."""
    out = preprocess_math(_esc(s))
    return out.replace('\n', '<br>')


# ---------------------------------------------------------------------------
# Shared CSS
# ---------------------------------------------------------------------------
#
# Font stack walks Latin -> CJK -> Tamil so a single string of mixed text
# resolves each glyph from the first family that has it. Noto families are
# the lingua franca on Linux servers; Source Han / AR PL UMing / PingFang
# are Mac/Windows fallbacks for local development.

_PDF_CSS = """
@page {
    size: A4;
    margin: 1.4cm 1.6cm;
    @bottom-right {
        content: counter(page) ' / ' counter(pages);
        font-size: 8.5pt;
        color: #888;
    }
}
* { box-sizing: border-box; }
html, body {
    font-family: 'Noto Serif', 'Noto Sans', 'Noto Serif CJK SC',
                 'Noto Sans CJK SC', 'Noto Sans Tamil', 'Noto Serif Tamil',
                 'Source Han Serif SC', 'PingFang SC', 'AR PL UMing CN',
                 'Helvetica', sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    color: #1a1a2e;
    margin: 0;
}
h1, h2, h3 { color: #1a1a2e; }
h1 { font-size: 18pt; margin: 0 0 4pt; }
h2 {
    font-size: 13pt; margin: 18pt 0 8pt;
    padding-bottom: 4pt; border-bottom: 1px solid #d6d6e0;
}
h3 { font-size: 11pt; margin: 10pt 0 4pt; }

.muted { color: #666; font-size: 9.5pt; }
hr { border: none; border-top: 1px solid #d6d6e0; margin: 12pt 0; }

.info-grid {
    display: grid; grid-template-columns: max-content 1fr max-content 1fr;
    gap: 4pt 12pt; padding: 8pt 10pt; background: #f4f5fb;
    border: 1px solid #d6d6e0; border-radius: 4pt; font-size: 9.5pt;
}
.info-grid .k { font-weight: bold; color: #444; }

.summary-row {
    display: flex; justify-content: space-around; align-items: center;
    margin: 14pt 0; padding: 10pt; background: white;
    border: 2px solid #4a54c4; border-radius: 6pt;
    color: #4a54c4; font-weight: bold; font-size: 11pt;
}
.summary-row .summary-cell { text-align: center; }
.summary-row .summary-cell .label { font-size: 8.5pt; color: #777; font-weight: normal; }

.banner {
    padding: 8pt 12pt; margin: 10pt 0; border-radius: 4pt;
    border-left: 4px solid; font-size: 10pt; line-height: 1.5;
}
.banner.well-done { background: #f0fdf4; border-color: #28a745; }
.banner.main-gap  { background: #fff8e1; border-color: #e68a00; }

.q-block {
    page-break-inside: avoid; margin: 10pt 0; border: 1px solid #d6d6e0;
    border-radius: 4pt; overflow: hidden;
}
.q-head {
    display: flex; justify-content: space-between; align-items: center;
    padding: 7pt 10pt; background: #4a54c4; color: white; font-weight: bold;
}
.q-head .status { padding: 2pt 8pt; border-radius: 999pt; font-size: 9pt; }
.q-head .status.correct           { background: #28a745; }
.q-head .status.partially_correct { background: #e68a00; }
.q-head .status.incorrect         { background: #dc3545; }
.q-body { padding: 0; }
.q-row { display: grid; grid-template-columns: 26mm 1fr; border-top: 1px solid #ececf2; }
.q-row:first-child { border-top: none; }
.q-row .k { padding: 6pt 8pt; background: #f7f8fc; font-weight: bold; font-size: 9.5pt; color: #444; }
.q-row .v { padding: 6pt 10pt; font-size: 10pt; }

table.errors, table.items, table.scores {
    width: 100%; border-collapse: collapse; font-size: 9.5pt;
    margin: 6pt 0 12pt; page-break-inside: auto;
}
table.errors th, table.items th, table.scores th {
    background: #4a54c4; color: white; text-align: left;
    padding: 6pt 8pt; font-weight: bold; font-size: 9.5pt;
}
table.errors td, table.items td, table.scores td {
    padding: 5pt 8pt; border-bottom: 1px solid #ececf2; vertical-align: top;
}
table.errors tr:nth-child(even) td,
table.items tr:nth-child(even) td,
table.scores tr:nth-child(even) td { background: #fafbff; }
.t-center { text-align: center; }
.diff-easy   { color: #28a745; font-weight: bold; }
.diff-mod    { color: #e68a00; font-weight: bold; }
.diff-hard   { color: #dc3545; font-weight: bold; }
.pct-pass    { color: #28a745; font-weight: bold; }
.pct-fail    { color: #dc3545; font-weight: bold; }
.struck      { text-decoration: line-through; color: #c0392b; }
.fix         { color: #1f7a3e; }

.actions { margin: 6pt 0 12pt; padding-left: 18pt; }
.actions li { margin-bottom: 3pt; font-size: 10pt; }

.footer {
    margin-top: 18pt; padding-top: 8pt; border-top: 1px solid #d6d6e0;
    font-size: 8.5pt; color: #888; text-align: center;
}

math { font-family: 'STIX Two Math', 'Cambria Math', 'Latin Modern Math', serif; }
math[display="block"] { display: block; text-align: center; margin: 4pt 0; }
code {
    background: #f3f4f6; padding: 1pt 4pt; border-radius: 2pt;
    font-family: 'Menlo', 'Consolas', monospace; font-size: 9pt;
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_LABEL = {
    'correct': 'Correct',
    'partially_correct': 'Partial',
    'incorrect': 'Incorrect',
}


def _status_class(status):
    """Map a status string to one of our three CSS classes; default to
    'incorrect' so unknown statuses surface in red rather than vanish."""
    return status if status in _STATUS_LABEL else 'incorrect'


def _render_html_to_pdf(html_str):
    """Convert a fully-assembled HTML string to PDF bytes."""
    HTML = _get_weasy()
    return HTML(string=html_str).write_pdf()


# ---------------------------------------------------------------------------
# generate_report_pdf — per-submission feedback report
# ---------------------------------------------------------------------------

def generate_report_pdf(result, subject='', app_title='AI Feedback Systems'):
    """Generate a per-submission feedback PDF.

    Args:
        result: Dict from mark_script() with `questions`, `overall_feedback`,
            optional `errors`, `recommended_actions`, `well_done`,
            `main_gap`, `assign_type`, `provider_label`, etc.
        subject: Subject string for the header.
        app_title: App title for the report heading.

    Returns:
        bytes — the rendered PDF.
    """
    questions = result.get('questions', []) or []
    is_rubrics = result.get('assign_type') == 'rubrics'
    has_marks = any(q.get('marks_awarded') is not None for q in questions)

    # --- Summary ---
    if has_marks:
        ta = sum((q.get('marks_awarded') or 0) for q in questions)
        tp = sum((q.get('marks_total') or 0) for q in questions)
        pct = round(ta / tp * 100) if tp > 0 else 0
        summary_html = (
            f'<div class="summary-cell"><div class="label">Score</div>{ta} / {tp}</div>'
            f'<div class="summary-cell"><div class="label">Percentage</div>{pct}%</div>'
            f'<div class="summary-cell"><div class="label">Questions</div>{len(questions)}</div>'
        )
    else:
        counts = {'correct': 0, 'partially_correct': 0, 'incorrect': 0}
        for q in questions:
            counts[_status_class(q.get('status', 'incorrect'))] = (
                counts.get(_status_class(q.get('status', 'incorrect')), 0) + 1
            )
        summary_html = (
            f'<div class="summary-cell"><div class="label">Correct</div>{counts["correct"]}</div>'
            f'<div class="summary-cell"><div class="label">Partial</div>{counts["partially_correct"]}</div>'
            f'<div class="summary-cell"><div class="label">Incorrect</div>{counts["incorrect"]}</div>'
            f'<div class="summary-cell"><div class="label">Total</div>{len(questions)}</div>'
        )

    # --- Banners ---
    banners = ''
    if result.get('well_done'):
        banners += f'<div class="banner well-done"><strong>✓ Well done:</strong> {_esc_md(result["well_done"])}</div>'
    if result.get('main_gap'):
        banners += f'<div class="banner main-gap"><strong>→ Main gap:</strong> {_esc_md(result["main_gap"])}</div>'

    # --- Per-question / per-criterion blocks ---
    section_title = 'Rubric Criteria Feedback' if is_rubrics else 'Question-by-Question Feedback'
    item_label = 'Criterion' if is_rubrics else 'Question'
    ans_label = 'Assessment' if is_rubrics else 'Student Answer'
    ref_label = 'Band Descriptor' if is_rubrics else 'Correct Answer'

    q_blocks = []
    for q in questions:
        status = _status_class(q.get('status', 'incorrect'))
        marks_text = ''
        if q.get('marks_awarded') is not None:
            mt = q.get('marks_total')
            mt_str = str(mt) if mt is not None else '?'
            marks_text = f' ({q["marks_awarded"]}/{mt_str})'

        criterion_name = q.get('criterion_name', '')
        band_info = q.get('band', '')
        if is_rubrics and criterion_name:
            head_left = _esc(criterion_name)
            if band_info:
                head_left += f' — <span style="font-weight:normal">{_esc(band_info)}</span>'
        else:
            head_left = f'{item_label} {_esc(q.get("question_num", "?"))}'

        rows_html = []
        rows_html.append(f'<div class="q-row"><div class="k">{ans_label}</div><div class="v">{_esc_md(q.get("student_answer", "N/A"))}</div></div>')
        rows_html.append(f'<div class="q-row"><div class="k">{ref_label}</div><div class="v">{_esc_md(q.get("correct_answer", "N/A"))}</div></div>')
        if q.get('feedback'):
            rows_html.append(f'<div class="q-row"><div class="k">Feedback</div><div class="v">{_esc_md(q["feedback"])}</div></div>')
        if q.get('improvement'):
            rows_html.append(f'<div class="q-row"><div class="k">Improvement</div><div class="v">{_esc_md(q["improvement"])}</div></div>')
        if q.get('correction_prompt'):
            rows_html.append(f'<div class="q-row"><div class="k">Try this</div><div class="v"><em>{_esc_md(q["correction_prompt"])}</em></div></div>')

        q_blocks.append(
            f'<div class="q-block">'
            f'<div class="q-head">'
            f'<span>{head_left}</span>'
            f'<span class="status {status}">{_STATUS_LABEL.get(status, "Incorrect")}{marks_text}</span>'
            f'</div>'
            f'<div class="q-body">{"".join(rows_html)}</div>'
            f'</div>'
        )

    # --- Errors table (rubrics mode) ---
    errors_html = ''
    errors = result.get('errors') or []
    if errors:
        rows = []
        for err in errors:
            rows.append(
                '<tr>'
                f'<td>{_esc(err.get("type", "")).upper()}</td>'
                f'<td>{_esc(err.get("location", ""))}</td>'
                f'<td><span class="struck">{_esc_md(err.get("original", ""))}</span></td>'
                f'<td><span class="fix">{_esc_md(err.get("correction", ""))}</span></td>'
                '</tr>'
            )
        errors_html = (
            f'<h2>Line-by-Line Errors ({len(errors)})</h2>'
            '<table class="errors">'
            '<thead><tr><th>Type</th><th>Location</th><th>Original</th><th>Correction</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>'
            '</table>'
        )

    # --- Overall + actions ---
    overall_html = f'<h2>Overall Feedback</h2><p>{_esc_md(result.get("overall_feedback", "No overall feedback provided."))}</p>'
    actions = result.get('recommended_actions') or []
    if actions:
        items = ''.join(f'<li>{_esc_md(a)}</li>' for a in actions)
        overall_html += f'<h3>Recommended Actions</h3><ol class="actions">{items}</ol>'

    # --- Header info grid ---
    now = datetime.now(timezone.utc).strftime('%d %B %Y, %H:%M UTC')
    provider_label = _esc(result.get('provider_label', result.get('provider', 'AI')))
    info_html = (
        '<div class="info-grid">'
        f'<div class="k">Subject:</div><div>{_esc(subject or "General")}</div>'
        f'<div class="k">Date:</div><div>{now}</div>'
        f'<div class="k">AI Provider:</div><div>{provider_label}</div>'
        '<div></div><div></div>'
        '</div>'
    )

    title = _esc(f'{app_title} Report' if app_title else 'AI Marking Report')
    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{_PDF_CSS}</style>
</head>
<body>
<h1 style="text-align:center">{title}</h1>
{info_html}
<div class="summary-row">{summary_html}</div>
{banners}
<h2>{section_title}</h2>
{"".join(q_blocks)}
{errors_html}
<hr>
{overall_html}
<div class="footer">Generated by {_esc(app_title or "AI Feedback Systems")}</div>
</body>
</html>"""

    return _render_html_to_pdf(html_doc)


# ---------------------------------------------------------------------------
# generate_overview_pdf — class overview + item analysis
# ---------------------------------------------------------------------------

def generate_overview_pdf(student_results, subject='', app_title='AI Feedback Systems'):
    """Generate a class overview PDF: stats summary, score distribution,
    item analysis, weak-area callouts, and a ranked individual scores
    table.

    Args:
        student_results: List of {'name', 'index', 'result'} dicts where
            `result` is a mark_script() output dict.
        subject: Subject string for the header.
        app_title: App title for the report heading.

    Returns:
        bytes — the rendered PDF.
    """
    valid = [sr for sr in student_results if sr.get('result') and not sr['result'].get('error')]

    # --- Per-student totals ---
    scores = []
    has_marks = False
    for sr in valid:
        questions = sr['result'].get('questions', []) or []
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

    # --- Class summary ---
    summary_block = ''
    if scores:
        pcts = [s['pct'] for s in scores]
        avg_pct = round(mean(pcts), 1)
        med_pct = round(median(pcts), 1)
        high_pct = max(pcts)
        low_pct = min(pcts)
        std_pct = round(stdev(pcts), 1) if len(pcts) > 1 else 0
        pass_count = sum(1 for p in pcts if p >= 50)
        pass_rate = round(pass_count / len(pcts) * 100)

        summary_block = (
            '<table class="items">'
            '<thead><tr>'
            '<th class="t-center">Mean</th><th class="t-center">Median</th>'
            '<th class="t-center">Highest</th><th class="t-center">Lowest</th>'
            '<th class="t-center">Std Dev</th><th class="t-center">Pass Rate</th>'
            '</tr></thead><tbody><tr>'
            f'<td class="t-center">{avg_pct}%</td>'
            f'<td class="t-center">{med_pct}%</td>'
            f'<td class="t-center">{high_pct}%</td>'
            f'<td class="t-center">{low_pct}%</td>'
            f'<td class="t-center">{std_pct}%</td>'
            f'<td class="t-center">{pass_rate}% ({pass_count}/{len(scores)})</td>'
            '</tr></tbody></table>'
        )

        bands = [('0-24%', 0, 24), ('25-49%', 25, 49), ('50-74%', 50, 74), ('75-100%', 75, 100)]
        band_counts = [sum(1 for p in pcts if lo <= p <= hi) for _, lo, hi in bands]
        summary_block += (
            '<h3>Score Distribution</h3>'
            '<table class="items">'
            '<thead><tr>' + ''.join(f'<th class="t-center">{b[0]}</th>' for b in bands) + '</tr></thead>'
            '<tbody><tr>' + ''.join(f'<td class="t-center">{c}</td>' for c in band_counts) + '</tr></tbody>'
            '</table>'
        )
    else:
        summary_block = '<p>No scored results available.</p>'

    # --- Item analysis ---
    question_stats = {}
    for sr in valid:
        for q in sr['result'].get('questions', []) or []:
            qn = q.get('question_num', '?')
            key = str(qn)
            qs = question_stats.setdefault(key, {
                'num': qn, 'criterion_name': q.get('criterion_name', ''),
                'correct': 0, 'partial': 0, 'incorrect': 0, 'total': 0,
                'marks_sum': 0, 'marks_max': 0,
            })
            qs['total'] += 1
            status = q.get('status', 'incorrect')
            if status == 'correct':
                qs['correct'] += 1
            elif status == 'partially_correct':
                qs['partial'] += 1
            else:
                qs['incorrect'] += 1
            if q.get('marks_awarded') is not None:
                qs['marks_sum'] += q.get('marks_awarded') or 0
                qs['marks_max'] = max(qs['marks_max'], q.get('marks_total') or 0)

    item_block = ''
    weak_block = ''
    if question_stats:
        sorted_qs = sorted(
            question_stats.values(),
            key=lambda x: (int(x['num']) if str(x['num']).isdigit() else 999, str(x['num'])),
        )
        is_rubrics = any(qs['criterion_name'] for qs in sorted_qs)
        q_label = 'Criterion' if is_rubrics else 'Q#'

        rows = []
        for qs in sorted_qs:
            n = qs['total'] or 1
            pct_correct = round(qs['correct'] / n * 100)
            difficulty = pct_correct
            diff_label = 'Easy' if difficulty >= 70 else ('Moderate' if difficulty >= 40 else 'Hard')
            diff_class = 'diff-easy' if difficulty >= 70 else ('diff-mod' if difficulty >= 40 else 'diff-hard')

            q_name = qs['criterion_name'] if (is_rubrics and qs['criterion_name']) else str(qs['num'])
            cells = [
                f'<td>{_esc(q_name)}</td>',
                f'<td class="t-center">{qs["correct"]} ({pct_correct}%)</td>',
                f'<td class="t-center">{qs["partial"]} ({round(qs["partial"]/n*100)}%)</td>',
                f'<td class="t-center">{qs["incorrect"]} ({round(qs["incorrect"]/n*100)}%)</td>',
            ]
            if has_marks:
                avg_marks = round(qs['marks_sum'] / n, 1)
                cells.append(f'<td class="t-center">{avg_marks}</td>')
                cells.append(f'<td class="t-center">{qs["marks_max"]}</td>')
            cells.append(f'<td class="t-center {diff_class}">{diff_label} ({difficulty}%)</td>')
            rows.append(f'<tr>{"".join(cells)}</tr>')

        if has_marks:
            head = f'<tr><th>{q_label}</th><th class="t-center">Correct</th><th class="t-center">Partial</th><th class="t-center">Incorrect</th><th class="t-center">Avg Marks</th><th class="t-center">Max</th><th class="t-center">Difficulty</th></tr>'
        else:
            head = f'<tr><th>{q_label}</th><th class="t-center">Correct</th><th class="t-center">Partial</th><th class="t-center">Incorrect</th><th class="t-center">Difficulty</th></tr>'
        item_block = f'<table class="items"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'

        # Weak areas (< 40% correct)
        weak = [qs for qs in sorted_qs if (qs['correct'] / max(qs['total'], 1) * 100) < 40]
        if weak:
            lines = []
            for qs in weak:
                q_name = qs['criterion_name'] if (is_rubrics and qs['criterion_name']) else f'Question {qs["num"]}'
                pct = round(qs['correct'] / max(qs['total'], 1) * 100)
                lines.append(
                    f'<li><strong>{_esc(q_name)}</strong> — only {pct}% of students answered correctly '
                    f'({qs["incorrect"]} incorrect, {qs["partial"]} partially correct out of {qs["total"]})</li>'
                )
            weak_block = '<h3>Areas Needing Attention</h3><ul class="actions">' + ''.join(lines) + '</ul>'

    # --- Individual scores ranked ---
    score_block = ''
    if scores:
        sorted_scores = sorted(scores, key=lambda x: x['pct'], reverse=True)
        rows = []
        for rank, s in enumerate(sorted_scores, 1):
            pct_class = 'pct-pass' if s['pct'] >= 50 else 'pct-fail'
            rows.append(
                '<tr>'
                f'<td class="t-center">{rank}</td>'
                f'<td>{_esc(s["index"])}</td>'
                f'<td>{_esc(s["name"])}</td>'
                f'<td class="t-center">{s["awarded"]}/{s["possible"]}</td>'
                f'<td class="t-center {pct_class}">{s["pct"]}%</td>'
                '</tr>'
            )
        score_block = (
            '<table class="scores">'
            '<thead><tr><th class="t-center">Rank</th><th>Index</th><th>Name</th>'
            '<th class="t-center">Score</th><th class="t-center">%</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
        )

    # --- Header info ---
    now = datetime.now(timezone.utc).strftime('%d %B %Y, %H:%M UTC')
    info_html = (
        '<div class="info-grid">'
        f'<div class="k">Subject:</div><div>{_esc(subject or "General")}</div>'
        f'<div class="k">Date:</div><div>{now}</div>'
        f'<div class="k">Total Students:</div><div>{len(student_results)}</div>'
        '<div></div><div></div>'
        '</div>'
    )

    title = _esc(f'{app_title} — Class Overview & Item Analysis')
    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{_PDF_CSS}</style>
</head>
<body>
<h1 style="text-align:center">{title}</h1>
{info_html}
<h2>Class Summary</h2>
{summary_block}
<h2>Item Analysis</h2>
{item_block or "<p>No item statistics available.</p>"}
{weak_block}
<h2>Individual Scores</h2>
{score_block or "<p>No scored students.</p>"}
<div class="footer">Generated by {_esc(app_title or "AI Feedback Systems")}</div>
</body>
</html>"""

    return _render_html_to_pdf(html_doc)


# Backwards-compat shim for any caller importing the old helper.
def clean_for_pdf(text):  # noqa: D401
    """Legacy helper kept so callers outside this module that imported
    `clean_for_pdf` keep working. The HTML pipeline never needs it."""
    return _esc(text)
