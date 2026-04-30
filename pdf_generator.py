"""
PDF generation via LuaLaTeX — proper math typesetting (true fractions,
matrices, etc.) with native CJK and Tamil through fontspec + Noto fonts.

Compiles a generated .tex string with `lualatex` in a temp dir, returns
the resulting PDF bytes. Public API:

  - generate_report_pdf(result, subject='', app_title='AI Feedback Systems',
                        assignment_name='') -> bytes
  - generate_overview_pdf(student_results, subject='', app_title='...',
                          assignment_name='') -> bytes

Each function memoises its output in an in-process LRU cache keyed on the
SHA-256 of (kind, result, subject, app_title, assignment_name). Repeat
downloads of the same submission within a single container's lifetime
return the cached PDF bytes in milliseconds. The cache is bounded
(_PDF_CACHE_MAX entries) to keep RSS predictable; on overflow the
least-recently-used entry is evicted.

If lualatex is missing or the compile fails, RuntimeError is raised with
the last 2KB of the LaTeX log so the failure is debuggable from the
caller's exception path.
"""
import io
import os
import re
import shutil
import subprocess
import tempfile
import logging
import hashlib
import json as _json
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from statistics import mean, median, stdev

logger = logging.getLogger(__name__)

LUALATEX_BIN = shutil.which('lualatex') or 'lualatex'
LUALATEX_TIMEOUT = 60  # seconds

# In-process PDF cache. OrderedDict gives O(1) LRU via move_to_end /
# popitem(last=False). Capped at 200 entries — at our typical 80-150 KB
# per PDF that's ~25-30 MB of RSS, which is small relative to gunicorn's
# baseline. A redeploy resets the cache, which is also when rendering
# code may have changed, so that's the right invalidation cadence too.
_PDF_CACHE_MAX = 200
_PDF_CACHE = OrderedDict()
_PDF_CACHE_LOCK = threading.Lock()


def _cache_key(kind, *parts):
    """SHA-256 over a sentinel-separated stream of stringified parts.
    Sentinel is required so that ('ab', 'c') and ('a', 'bc') hash
    differently. Dicts are JSON-encoded with sorted keys for stable
    output across Python versions."""
    h = hashlib.sha256(kind.encode('utf-8'))
    for p in parts:
        if isinstance(p, (dict, list)):
            p = _json.dumps(p, sort_keys=True, default=str, ensure_ascii=False)
        h.update(b'\x00')
        h.update(('' if p is None else str(p)).encode('utf-8'))
    return h.hexdigest()


def _cache_get(key):
    with _PDF_CACHE_LOCK:
        if key in _PDF_CACHE:
            _PDF_CACHE.move_to_end(key)
            return _PDF_CACHE[key]
    return None


def _cache_put(key, pdf_bytes):
    with _PDF_CACHE_LOCK:
        _PDF_CACHE[key] = pdf_bytes
        _PDF_CACHE.move_to_end(key)
        while len(_PDF_CACHE) > _PDF_CACHE_MAX:
            _PDF_CACHE.popitem(last=False)


# ---------------------------------------------------------------------------
# Text → LaTeX with math preservation
# ---------------------------------------------------------------------------
#
# AI-generated feedback is a mix of three things in one string:
#   1. plain prose ("the student didn't show working...")
#   2. LaTeX math wrapped in $...$ or $$...$$
#   3. bare LaTeX-looking fragments without delimiters (e.g. "m s^{-2}",
#      "t^3", "\frac{a}{b}", "\alpha")
#
# Strategy: extract math fragments to opaque placeholders FIRST so the
# next pass (LaTeX-escape the prose) can't mangle them, then restore.

def _tex_escape(s):
    """Escape LaTeX special characters in plain prose. Order matters:
    multi-char escapes are stashed under sentinel chars first so the
    single-char brace escape doesn't mangle them, then restored.
    """
    if s is None:
        return ''
    s = str(s)
    s = s.replace('\\', '\x01BSL\x01')
    s = s.replace('~', '\x01TLD\x01')
    s = s.replace('^', '\x01CRT\x01')
    s = s.replace('&', r'\&')
    s = s.replace('%', r'\%')
    s = s.replace('$', r'\$')
    s = s.replace('#', r'\#')
    s = s.replace('_', r'\_')
    s = s.replace('{', r'\{')
    s = s.replace('}', r'\}')
    s = s.replace('\x01BSL\x01', r'\textbackslash{}')
    s = s.replace('\x01TLD\x01', r'\textasciitilde{}')
    s = s.replace('\x01CRT\x01', r'\textasciicircum{}')
    return s


# Bare-math patterns we'll auto-wrap in \(...\). Order = priority.
_BARE_MATH_PATTERNS = [
    re.compile(r'\\frac\s*\{[^{}]*\}\s*\{[^{}]*\}'),
    re.compile(r'\\sqrt\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}'),
    re.compile(r'\b\w+(?:\^|_)(?:\{[^{}]*\}|-?\w+)'),
    # Whitelist of bare LaTeX commands worth escaping into math mode. We
    # don't auto-wrap *every* \word because false positives in prose
    # ("the \emph article") would silently disappear.
    re.compile(
        r'\\(?:alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|'
        r'lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega|'
        r'Alpha|Beta|Gamma|Delta|Theta|Lambda|Pi|Sigma|Phi|Omega|'
        r'le|leq|ge|geq|neq|ne|approx|equiv|sim|propto|infty|partial|'
        r'sum|prod|int|to|rightarrow|leftarrow|Rightarrow|implies|iff|'
        r'therefore|because|degree|circ|angle|perp|times|cdot|div|pm|mp|'
        r'in|notin|subset|supset|cap|cup|forall|exists|emptyset|'
        r'cdots|ldots|dots|prime|hbar|ell)\b'
    ),
]


def _tex_text(s):
    """Convert AI text to LaTeX-safe content while preserving math.

    Order:
      1. extract $$...$$ blocks → \\[...\\] (display math)
      2. extract $...$ blocks → \\(...\\) (inline math)
      3. extract bare math patterns (\\frac, ^_, named cmds) → \\(...\\)
      4. LaTeX-escape the remaining prose
      5. restore all extracted math fragments
    """
    if not s:
        return ''
    s = str(s)
    placeholders = []

    def stash(payload):
        placeholders.append(payload)
        return f'\x02M{len(placeholders) - 1}M\x02'

    s = re.sub(
        r'\$\$(.+?)\$\$',
        lambda m: stash(r'\[' + m.group(1).strip() + r'\]'),
        s, flags=re.DOTALL,
    )
    s = re.sub(
        r'(?<!\$)\$(?!\$)((?:[^$\n]|\n)+?)(?<!\$)\$(?!\$)',
        lambda m: stash(r'\(' + m.group(1).strip() + r'\)'),
        s,
    )
    for pat in _BARE_MATH_PATTERNS:
        s = pat.sub(lambda m: stash(r'\(' + m.group(0) + r'\)'), s)

    s = _tex_escape(s)
    s = re.sub(r'\x02M(\d+)M\x02', lambda m: placeholders[int(m.group(1))], s)
    return s


def _tex_inline(s):
    """LaTeX-escape a short single-line value (subject, name, etc.) that
    won't carry math. Newlines collapse to spaces."""
    if s is None:
        return ''
    return _tex_escape(str(s).replace('\n', ' ').replace('\r', ''))


# ---------------------------------------------------------------------------
# LaTeX preamble — shared by report and overview documents
# ---------------------------------------------------------------------------

_PREAMBLE = r"""\documentclass[10pt,a4paper]{article}

\usepackage[a4paper,top=1.5cm,bottom=1.8cm,left=1.5cm,right=1.5cm]{geometry}
\usepackage{fontspec}
\usepackage[table,svgnames,dvipsnames]{xcolor}
\usepackage{tabularx}
\usepackage{array}
\usepackage{colortbl}
\usepackage{booktabs}
\usepackage{enumitem}
\usepackage{amsmath,amssymb}
% mhchem mirrors the KaTeX mhchem extension loaded in base.html, so
% \ce{H_2O} / \pu{1.5 mol/L} render identically in the browser and PDF.
% Provided by texlive-science on Debian.
\usepackage[version=4]{mhchem}
\usepackage{titlesec}
\usepackage{ulem}

% TeX Gyre Heros is the open Helvetica clone, apt-installed in
% texlive-fonts-recommended on Railway and visually indistinguishable
% from Helvetica. The multi-script fallback chain routes any non-Latin
% codepoint to Noto (CJK / Tamil / Devanagari) so a name like "王晓明"
% or "முத்து" renders correctly inside an otherwise-Latin PDF — no
% content detection needed; the engine handles it per-glyph.
%
% \IfFontExistsTF guards: if Noto Sans CJK SC isn't installed (local
% macOS dev), registering the fallback chain corrupts the main font
% load, so we skip the fallback in that case. Production always has
% Noto via the apt fonts-noto-cjk package.
\IfFontExistsTF{Noto Sans CJK SC}{%
  \directlua{
    luaotfload.add_fallback("multilang_fb", {
      "Noto Sans CJK SC:script=hani;",
      "Noto Sans Tamil:script=taml;",
      "Noto Sans Devanagari:script=deva;",
    })
  }
  \setmainfont{TeX Gyre Heros}[RawFeature={fallback=multilang_fb}]
}{%
  \setmainfont{TeX Gyre Heros}
}

% Brand palette (matches the previous PDF look)
\definecolor{brandblue}{HTML}{4A54C4}
\definecolor{brandgreen}{HTML}{28A745}
\definecolor{brandorange}{HTML}{E68A00}
\definecolor{brandred}{HTML}{DC3545}
\definecolor{bggreen}{HTML}{F0FDF4}
\definecolor{bgorange}{HTML}{FFF8E1}
\definecolor{bggrey}{HTML}{F4F5FB}
\definecolor{bordergrey}{HTML}{D6D6E0}
\definecolor{textmuted}{HTML}{666666}

% Tight spacing — explicit goal: minimise empty space between blocks.
\setlength{\parindent}{0pt}
\setlength{\parskip}{3pt}
\setlist{nosep,leftmargin=1.5em,topsep=2pt,partopsep=0pt}
\titlespacing*{\section}{0pt}{8pt}{2pt}
\titlespacing*{\subsection}{0pt}{6pt}{2pt}
\titleformat{\section}{\bfseries\large\color{black}}{}{0pt}{}[\vspace{-2pt}{\color{bordergrey}\hrule height 0.4pt}]
\titleformat{\subsection}{\bfseries\normalsize\color{black}}{}{0pt}{}

\renewcommand{\arraystretch}{1.18}

% Stacked label/value cell for the summary row. #1 = width fraction,
% #2 = label, #3 = value. Used inside a fcolorbox-bordered minipage.
\newcommand{\summarycell}[3]{%
  \begin{minipage}[c]{#1\linewidth}\centering
    {\small\color{textmuted} #2}\par\vspace{1pt}
    {\Large\bfseries\color{brandblue} #3}
  \end{minipage}%
}
"""


# ---------------------------------------------------------------------------
# Body builders
# ---------------------------------------------------------------------------

_STATUS_LABEL = {
    'correct': 'Correct',
    'partially_correct': 'Partial',
    'incorrect': 'Incorrect',
}
_STATUS_COLOR = {
    'correct': 'brandgreen',
    'partially_correct': 'brandorange',
    'incorrect': 'brandred',
}


def _build_info_grid(rows):
    """Return TeX for a 4-column key/value grid (label, value, label, value).
    `rows` is a list of (label, value) tuples; we pair them two per row."""
    cells = []
    pairs = list(rows)
    while pairs:
        l1, v1 = pairs.pop(0)
        if pairs:
            l2, v2 = pairs.pop(0)
        else:
            l2, v2 = '', ''
        cells.append(f'\\textbf{{{_tex_inline(l1)}:}} & {_tex_inline(v1)} & '
                     f'\\textbf{{{_tex_inline(l2)}{":" if l2 else ""}}} & {_tex_inline(v2)} \\\\')
    body = '\n'.join(cells)
    return (
        r'\noindent\rowcolors{1}{bggrey}{bggrey}' + '\n'
        r'\begin{tabularx}{\linewidth}{@{\hspace{6pt}}>{\bfseries}p{2.4cm} X >{\bfseries}p{2.0cm} X@{\hspace{6pt}}}' + '\n'
        + body + '\n'
        r'\end{tabularx}'
    )


def _build_summary_row(items):
    """Summary box with N centered stacked cells.

    Switched from tcolorbox to \\fcolorbox so the preamble can drop the
    tcolorbox package (~300-500ms shaved off every compile)."""
    n = max(1, len(items))
    width = round(0.93 / n, 3)
    cells = []
    for lbl, val in items:
        cells.append(rf'\summarycell{{{width}}}{{{_tex_inline(lbl)}}}{{{_tex_inline(val)}}}')
    body = r'\hfill '.join(cells)
    return (
        r'\par\noindent' + '\n'
        r'{\setlength{\fboxsep}{8pt}\setlength{\fboxrule}{1pt}%' + '\n'
        r'\fcolorbox{brandblue}{white}{%' + '\n'
        r'\begin{minipage}{\dimexpr\linewidth-2\fboxsep-2\fboxrule}' + '\n'
        + body + '\n'
        r'\end{minipage}}}\par' + '\n'
    )


def _build_qcard(label, status_key, marks_text, rows):
    """One per-question / per-criterion card, matching the old PDF layout:

      [ brand-blue band: label                    | status colour: status pill ]
      | Student Answer (bggrey, bold)             | <answer text>             |
      |-------------------------------------------+---------------------------|
      | Correct Answer (bggrey, bold)             | ...                       |
      |-------------------------------------------+---------------------------|
      | Feedback (bggrey, bold)                   | ...                       |
      |-------------------------------------------+---------------------------|
      | Improvement (bggrey, bold)                | ...                       |

    Header is a separate (no-border) tabular with a 70/30 split to keep
    the pill comfortably sized regardless of label length. Body is a
    tabularx with grey \\hline separators between rows and \\arrayrulecolor
    set to bordergrey for the outer border too. No tcolorbox wrapper —
    the table's own borders give us the card outline."""
    color = _STATUS_COLOR.get(status_key, 'brandred')
    status_label = _STATUS_LABEL.get(status_key, 'Incorrect')
    pill_text = status_label + (marks_text or '')

    body_rows = []
    for k, v in rows:
        if v is None or v == '':
            continue
        body_rows.append(rf'{_tex_inline(k)} & {_tex_text(v)} \\')
    if not body_rows:
        body = r'\multicolumn{2}{|l|}{\itshape (no detail)} \\' + '\n' + r'\hline'
    else:
        body = ('\n' + r'\hline' + '\n').join(body_rows) + '\n' + r'\hline'

    # Labels: regular weight on bggrey background — the column shading
    # provides enough visual separation without bold.
    return (
        r'\par\noindent' + '\n'
        r'{\setlength{\fboxsep}{6pt}%' + '\n'
        r'\colorbox{brandblue}{%' + '\n'
        r'  \rule[-3pt]{0pt}{16pt}\bfseries\color{white}%' + '\n'
        rf'  \makebox[\dimexpr 0.70\linewidth-2\fboxsep][l]{{{_tex_inline(label)}}}%' + '\n'
        r'}'
        rf'\colorbox{{{color}}}{{%' + '\n'
        r'  \rule[-3pt]{0pt}{16pt}\bfseries\color{white}%' + '\n'
        rf'  \makebox[\dimexpr 0.30\linewidth-2\fboxsep][c]{{{_tex_inline(pill_text)}}}%' + '\n'
        r'}}\par\nointerlineskip' + '\n'
        r'{\arrayrulecolor{bordergrey}\setlength{\arrayrulewidth}{0.4pt}%' + '\n'
        r'\renewcommand{\arraystretch}{1.15}%' + '\n'
        r'\begin{tabularx}{\linewidth}{|>{\columncolor{bggrey}}p{3cm}|X|}' + '\n'
        + body + '\n'
        r'\end{tabularx}}' + '\n'
        r'\vspace{6pt}' + '\n'
    )


# ---------------------------------------------------------------------------
# generate_report_pdf
# ---------------------------------------------------------------------------

def generate_report_pdf(result, subject='', app_title='AI Feedback Systems',
                        assignment_name=''):
    """Build a single-submission feedback report and return its PDF bytes.
    Memoised on (kind, result, subject, app_title, assignment_name)."""
    cache_key = _cache_key('report', result, subject, app_title, assignment_name)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    pdf = _generate_report_pdf_impl(result, subject, app_title, assignment_name)
    _cache_put(cache_key, pdf)
    return pdf


def _generate_report_pdf_impl(result, subject, app_title, assignment_name):
    questions = result.get('questions', []) or []
    is_rubrics = result.get('assign_type') == 'rubrics'
    has_marks = any(q.get('marks_awarded') is not None for q in questions)

    now = datetime.now(timezone.utc).strftime('%d %B %Y, %H:%M UTC')

    # Header info grid: Subject + Date on row 1, Assignment on row 2.
    info = _build_info_grid([
        ('Subject', subject or 'General'),
        ('Date', now),
        ('Assignment', assignment_name or '—'),
    ])

    # Summary row
    if has_marks:
        ta = sum((q.get('marks_awarded') or 0) for q in questions)
        tp = sum((q.get('marks_total') or 0) for q in questions)
        pct = round(ta / tp * 100) if tp > 0 else 0
        summary = _build_summary_row([
            ('Score', f'{ta} / {tp}'),
            # Pass raw '%'; _tex_inline escapes it once into \%. Pre-escaping
            # here would feed '\%' into _tex_inline, which would then escape
            # the backslash too and emit "\textbackslash{}\%".
            ('Percentage', f'{pct}%'),
            ('Questions', str(len(questions))),
        ])
    else:
        counts = {'correct': 0, 'partially_correct': 0, 'incorrect': 0}
        for q in questions:
            k = q.get('status', 'incorrect')
            if k in counts:
                counts[k] += 1
        summary = _build_summary_row([
            ('Correct', str(counts['correct'])),
            ('Partial', str(counts['partially_correct'])),
            ('Incorrect', str(counts['incorrect'])),
            ('Total', str(len(questions))),
        ])

    # Banners — tabular with a 4pt coloured left rule + tinted background.
    # Replaces the old tcolorbox-based wellbanner / gapbanner so we can
    # drop tcolorbox from the preamble (~300-500ms saved per compile).
    banners = []

    def _banner(rule_color, bg_color, prefix_tex, body_text):
        return (
            r'\par\noindent' + '\n'
            rf'{{\arrayrulecolor{{{rule_color}}}\setlength{{\arrayrulewidth}}{{4pt}}%' + '\n'
            r'\renewcommand{\arraystretch}{1.15}%' + '\n'
            r'\begin{tabular}{|@{\hspace{8pt}}p{\dimexpr\linewidth-4pt-2\tabcolsep-8pt}@{}}' + '\n'
            rf'\rowcolor{{{bg_color}}} {prefix_tex} ' + _tex_text(body_text) + r' \\' + '\n'
            r'\end{tabular}}\par' + '\n'
        )

    if result.get('well_done'):
        banners.append(_banner(
            'brandgreen', 'bggreen',
            r'\textbf{$\checkmark$ Well done:}',
            result['well_done'],
        ))
    if result.get('main_gap'):
        banners.append(_banner(
            'brandorange', 'bgorange',
            r'\textbf{$\rightarrow$ Main gap:}',
            result['main_gap'],
        ))

    # Per-question cards
    section_title = 'Rubric Criteria Feedback' if is_rubrics else 'Question-by-Question Feedback'
    item_label = 'Criterion' if is_rubrics else 'Question'
    ans_label = 'Assessment' if is_rubrics else 'Student Answer'
    ref_label = 'Band Descriptor' if is_rubrics else 'Correct Answer'

    cards = []
    for q in questions:
        status = q.get('status', 'incorrect')
        marks_text = ''
        if q.get('marks_awarded') is not None:
            mt = q.get('marks_total')
            mt_str = str(mt) if mt is not None else '?'
            marks_text = f' ({q["marks_awarded"]}/{mt_str})'

        criterion_name = q.get('criterion_name', '')
        band_info = q.get('band', '')
        if is_rubrics and criterion_name:
            label = criterion_name
            if band_info:
                label += f' — {band_info}'
        else:
            label = f'{item_label} {q.get("question_num", "?")}'

        rows = [
            (ans_label, q.get('student_answer', 'N/A')),
            (ref_label, q.get('correct_answer', 'N/A')),
            ('Feedback', q.get('feedback', '')),
            ('Improvement', q.get('improvement', '')),
        ]
        # `correction_prompt` ("Try this") intentionally not rendered in
        # the PDF — kept on the data shape but no row in the report.
        cards.append(_build_qcard(label, status, marks_text, rows))

    # Errors table (rubrics only). ulem is loaded in the preamble; we just
    # use \sout here to strike through the original text.
    errors = result.get('errors') or []
    err_block = ''
    if errors:
        rows_tex = []
        for err in errors:
            etype = _tex_inline(err.get('type', '')).upper()
            loc = _tex_inline(err.get('location', ''))
            orig = r'\sout{' + _tex_text(err.get('original', '')) + r'}'
            corr = r'\textcolor{brandgreen}{' + _tex_text(err.get('correction', '')) + r'}'
            rows_tex.append(rf'{etype} & {loc} & {orig} & {corr} \\')
        err_block = (
            rf'\section*{{Line-by-Line Errors ({len(errors)})}}' + '\n'
            r'\rowcolors{2}{bggrey}{white}' + '\n'
            r'\begin{tabularx}{\linewidth}{@{}>{\bfseries\small}p{2cm} >{\small}p{2.5cm} >{\small}X >{\small}X@{}}' + '\n'
            r'\rowcolor{brandblue}\color{white}\textbf{Type} & '
            r'\color{white}\textbf{Location} & '
            r'\color{white}\textbf{Original} & '
            r'\color{white}\textbf{Correction} \\' + '\n'
            + '\n'.join(rows_tex) + '\n'
            r'\end{tabularx}'
        )

    # Overall + recommended actions
    overall_tex = _tex_text(result.get('overall_feedback', 'No overall feedback provided.'))
    actions = result.get('recommended_actions') or []
    actions_block = ''
    if actions:
        items = '\n'.join(rf'\item {_tex_text(a)}' for a in actions)
        actions_block = (
            r'\subsection*{Recommended Actions}' + '\n'
            r'\begin{enumerate}' + '\n' + items + '\n' + r'\end{enumerate}'
        )

    # No top title and no bottom footer — the PDF starts directly with
    # the info grid (Subject / Date / Assignment) per current design.
    body = '\n'.join([
        info,
        summary,
        '\n'.join(banners),
        rf'\section*{{{section_title}}}',
        '\n'.join(cards),
        err_block,
        r'\section*{Overall Feedback}',
        overall_tex,
        actions_block,
    ])

    tex = _PREAMBLE + r'\begin{document}' + '\n' + body + '\n' + r'\end{document}' + '\n'
    return _compile_tex_to_pdf(tex, jobname='report')


# ---------------------------------------------------------------------------
# generate_overview_pdf
# ---------------------------------------------------------------------------

def generate_overview_pdf(student_results, subject='', app_title='AI Feedback Systems',
                          assignment_name=''):
    """Class-overview PDF. Memoised on the result + assignment metadata."""
    cache_key = _cache_key(
        'overview',
        # student_results already carries unique submission ids + result
        # blobs; encoding the whole list is cheap relative to a compile.
        student_results, subject, app_title, assignment_name,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    pdf = _generate_overview_pdf_impl(student_results, subject, app_title, assignment_name)
    _cache_put(cache_key, pdf)
    return pdf


def _generate_overview_pdf_impl(student_results, subject, app_title, assignment_name):
    valid = [sr for sr in student_results if sr.get('result') and not sr['result'].get('error')]

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

    now = datetime.now(timezone.utc).strftime('%d %B %Y, %H:%M UTC')
    info = _build_info_grid([
        ('Subject', subject or 'General'),
        ('Date', now),
        ('Assignment', assignment_name or '—'),
        ('Total Students', str(len(student_results))),
    ])

    # Class summary (mean / median / etc.)
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

        stat_rows = (
            r'\rowcolor{brandblue}\color{white}\textbf{Mean} & '
            r'\color{white}\textbf{Median} & '
            r'\color{white}\textbf{Highest} & '
            r'\color{white}\textbf{Lowest} & '
            r'\color{white}\textbf{Std Dev} & '
            r'\color{white}\textbf{Pass Rate} \\'
            '\n'
            rf'{avg_pct}\% & {med_pct}\% & {high_pct}\% & {low_pct}\% & {std_pct}\% & '
            rf'{pass_rate}\% ({pass_count}/{len(scores)}) \\'
        )
        summary_block = (
            r'\section*{Class Summary}' + '\n'
            r'\begin{tabularx}{\linewidth}{@{}>{\centering\arraybackslash}X >{\centering\arraybackslash}X >{\centering\arraybackslash}X >{\centering\arraybackslash}X >{\centering\arraybackslash}X >{\centering\arraybackslash}X@{}}' + '\n'
            + stat_rows + '\n'
            r'\end{tabularx}'
        )

        # Score distribution bands. The labels use \% (escaped percent)
        # because any unescaped % in LaTeX starts a comment that eats the
        # rest of the line — including the \\ row terminator.
        bands = [(r'0--24\%', 0, 24), (r'25--49\%', 25, 49),
                 (r'50--74\%', 50, 74), (r'75--100\%', 75, 100)]
        band_counts = [sum(1 for p in pcts if lo <= p <= hi) for _, lo, hi in bands]
        band_header = ' & '.join(rf'\rowcolor{{brandblue}}\color{{white}}\textbf{{{b[0]}}}' if i == 0 else
                                 rf'\color{{white}}\textbf{{{b[0]}}}' for i, b in enumerate(bands))
        band_row = ' & '.join(str(c) for c in band_counts) + r' \\'
        summary_block += '\n' + (
            r'\subsection*{Score Distribution}' + '\n'
            r'\begin{tabularx}{\linewidth}{@{}>{\centering\arraybackslash}X >{\centering\arraybackslash}X >{\centering\arraybackslash}X >{\centering\arraybackslash}X@{}}' + '\n'
            + band_header + r' \\' + '\n'
            + band_row + '\n'
            r'\end{tabularx}'
        )
    else:
        summary_block = r'\section*{Class Summary}' + '\nNo scored results available.'

    # Item analysis
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
        q_label = 'Criterion' if is_rubrics else 'Q\\#'

        rows = []
        for qs in sorted_qs:
            n = qs['total'] or 1
            pct_correct = round(qs['correct'] / n * 100)
            difficulty = pct_correct
            diff_label = 'Easy' if difficulty >= 70 else ('Moderate' if difficulty >= 40 else 'Hard')
            diff_color = 'brandgreen' if difficulty >= 70 else ('brandorange' if difficulty >= 40 else 'brandred')

            q_name = qs['criterion_name'] if (is_rubrics and qs['criterion_name']) else str(qs['num'])
            cells = [
                _tex_inline(q_name),
                f'{qs["correct"]} ({pct_correct}\\%)',
                f'{qs["partial"]} ({round(qs["partial"]/n*100)}\\%)',
                f'{qs["incorrect"]} ({round(qs["incorrect"]/n*100)}\\%)',
            ]
            if has_marks:
                cells.append(str(round(qs['marks_sum'] / n, 1)))
                cells.append(str(qs['marks_max']))
            cells.append(rf'\textcolor{{{diff_color}}}{{\textbf{{{diff_label}}} ({difficulty}\%)}}')
            rows.append(' & '.join(cells) + r' \\')

        if has_marks:
            cols = r'@{}>{\bfseries}p{3cm} >{\centering\arraybackslash}p{1.6cm} >{\centering\arraybackslash}p{1.6cm} >{\centering\arraybackslash}p{1.6cm} >{\centering\arraybackslash}p{1.5cm} >{\centering\arraybackslash}p{1.2cm} >{\centering\arraybackslash}X@{}'
            head = (
                r'\rowcolor{brandblue}'
                rf'\color{{white}}\textbf{{{q_label}}} & '
                r'\color{white}\textbf{Correct} & '
                r'\color{white}\textbf{Partial} & '
                r'\color{white}\textbf{Incorrect} & '
                r'\color{white}\textbf{Avg} & '
                r'\color{white}\textbf{Max} & '
                r'\color{white}\textbf{Difficulty} \\'
            )
        else:
            cols = r'@{}>{\bfseries}p{3.5cm} >{\centering\arraybackslash}p{2cm} >{\centering\arraybackslash}p{2cm} >{\centering\arraybackslash}p{2cm} >{\centering\arraybackslash}X@{}'
            head = (
                r'\rowcolor{brandblue}'
                rf'\color{{white}}\textbf{{{q_label}}} & '
                r'\color{white}\textbf{Correct} & '
                r'\color{white}\textbf{Partial} & '
                r'\color{white}\textbf{Incorrect} & '
                r'\color{white}\textbf{Difficulty} \\'
            )
        item_block = (
            r'\section*{Item Analysis}' + '\n'
            r'\rowcolors{2}{bggrey}{white}' + '\n'
            rf'\begin{{tabularx}}{{\linewidth}}{{{cols}}}' + '\n'
            + head + '\n' + '\n'.join(rows) + '\n'
            r'\end{tabularx}'
        )

        weak = [qs for qs in sorted_qs if (qs['correct'] / max(qs['total'], 1) * 100) < 40]
        if weak:
            lines = []
            for qs in weak:
                q_name = qs['criterion_name'] if (is_rubrics and qs['criterion_name']) else f'Question {qs["num"]}'
                pct = round(qs['correct'] / max(qs['total'], 1) * 100)
                lines.append(
                    rf'\item \textbf{{{_tex_inline(q_name)}}} — only {pct}\% correct '
                    rf'({qs["incorrect"]} incorrect, {qs["partial"]} partial of {qs["total"]})'
                )
            weak_block = (
                r'\subsection*{Areas Needing Attention}' + '\n'
                r'\begin{itemize}' + '\n' + '\n'.join(lines) + '\n' + r'\end{itemize}'
            )

    # Individual scores ranked
    score_block = ''
    if scores:
        sorted_scores = sorted(scores, key=lambda x: x['pct'], reverse=True)
        rows = []
        for rank, s in enumerate(sorted_scores, 1):
            pct_color = 'brandgreen' if s['pct'] >= 50 else 'brandred'
            rows.append(
                rf'{rank} & {_tex_inline(s["index"])} & {_tex_inline(s["name"])} & '
                rf'{s["awarded"]}/{s["possible"]} & '
                rf'\textcolor{{{pct_color}}}{{\textbf{{{s["pct"]}\%}}}} \\'
            )
        score_block = (
            r'\section*{Individual Scores}' + '\n'
            r'\rowcolors{2}{bggrey}{white}' + '\n'
            r'\begin{tabularx}{\linewidth}{@{}>{\centering\arraybackslash}p{1cm} >{\centering\arraybackslash}p{2cm} X >{\centering\arraybackslash}p{2cm} >{\centering\arraybackslash}p{2cm}@{}}' + '\n'
            r'\rowcolor{brandblue}'
            r'\color{white}\textbf{Rank} & \color{white}\textbf{Index} & '
            r'\color{white}\textbf{Name} & \color{white}\textbf{Score} & '
            r'\color{white}\textbf{\%} \\' + '\n'
            + '\n'.join(rows) + '\n'
            r'\end{tabularx}'
        )

    # No top title and no bottom footer — the PDF starts with the info
    # grid and ends after the individual-scores table.
    body = '\n\n'.join(filter(None, [
        info,
        summary_block,
        item_block,
        weak_block,
        score_block,
    ]))

    tex = _PREAMBLE + r'\begin{document}' + '\n' + body + '\n' + r'\end{document}' + '\n'
    return _compile_tex_to_pdf(tex, jobname='overview')


# ---------------------------------------------------------------------------
# Compile helper
# ---------------------------------------------------------------------------

def _compile_tex_to_pdf(tex_str, jobname='report'):
    """Compile a .tex string to PDF via lualatex. Returns bytes; raises
    RuntimeError on failure with the last 2KB of the LaTeX log so callers
    can debug without trawling production logs."""
    with tempfile.TemporaryDirectory(prefix='aimark_tex_') as tmpdir:
        tex_path = os.path.join(tmpdir, f'{jobname}.tex')
        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(tex_str)
        try:
            proc = subprocess.run(
                [
                    LUALATEX_BIN,
                    '-interaction=nonstopmode',
                    '-halt-on-error',
                    '-output-directory', tmpdir,
                    tex_path,
                ],
                capture_output=True,
                timeout=LUALATEX_TIMEOUT,
                cwd=tmpdir,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"lualatex not found at {LUALATEX_BIN!r}. "
                "On Linux install texlive-luatex; on macOS install BasicTeX or MacTeX."
            ) from e
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"lualatex timed out after {LUALATEX_TIMEOUT}s")

        pdf_path = os.path.join(tmpdir, f'{jobname}.pdf')
        if not os.path.exists(pdf_path):
            log_tail = ''
            log_path = os.path.join(tmpdir, f'{jobname}.log')
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                        log_tail = f.read()[-2000:]
                except Exception:
                    pass
            stderr_tail = (proc.stderr or b'').decode('utf-8', errors='replace')[-500:]
            logger.error(
                "lualatex compile failed (rc=%s).\nLog tail:\n%s\nStderr:\n%s",
                proc.returncode, log_tail, stderr_tail,
            )
            raise RuntimeError(
                f"lualatex compile failed (rc={proc.returncode}). "
                f"Log tail:\n{log_tail[-800:]}"
            )

        with open(pdf_path, 'rb') as f:
            return f.read()


# Backwards-compat shim — older callers (none currently) imported this.
def clean_for_pdf(text):  # noqa: D401
    """Legacy helper. The new pipeline doesn't need it; we return an
    inline-escaped string for any caller that still imports it."""
    return _tex_inline(text)
