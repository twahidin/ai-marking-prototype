"""Hanyu Pinyin annotation for Chinese feedback text.

Public surface:
    annotate(text, mode='vocab', hsk_threshold=4) -> str

Modes:
    'off'   — return text unchanged.
    'vocab' — annotate only words at or above `hsk_threshold` (default
              HSK 4+) as ruby tags. Words below threshold and any non-CJK
              (Latin, digits, punctuation, math) pass through untouched.
    'full'  — annotate every CJK character with pinyin.

Production callers should run AI Chinese feedback through this AFTER the
mother-tongue prompt switch produces native-language fields, and BEFORE
storing the rendered HTML in Submission.result_json.

Output is HTML safe to drop into existing feedback templates: the
non-Chinese portions are HTML-escaped, ruby tags are emitted with bare
text content (no nested HTML), and there are no script / style hooks.

Heteronym handling: pypinyin uses jieba-style segmentation context to
pick the right tone (e.g. 重 → zhòng vs chóng). pypinyin has its own
internal segmenter; jieba is imported separately for our HSK lookup so
multi-char words match the HSK list cleanly.
"""

import json
import os
import re
import html
from functools import lru_cache

# Lazy-imported on first call so the rest of the app doesn't pay the
# jieba dictionary load cost (~400 ms) at import time.
_jieba = None
_pypinyin = None


def _ensure_libs():
    global _jieba, _pypinyin
    if _jieba is None:
        import jieba
        _jieba = jieba
    if _pypinyin is None:
        from pypinyin import pinyin, Style
        _pypinyin = (pinyin, Style)
    return _jieba, _pypinyin


HSK_PATH = os.path.join(os.path.dirname(__file__), 'data', 'hsk_words.json')


@lru_cache(maxsize=1)
def _hsk_map():
    """word (simplified) -> HSK level (1..6). Anything not present is
    treated as ≥ HSK 4 by callers using the default threshold, since
    most off-list characters in feedback prose are at-or-beyond
    secondary-vocabulary level."""
    if not os.path.exists(HSK_PATH):
        return {}
    with open(HSK_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


# CJK Unified Ideographs range. We treat any character in this range as
# "Chinese for annotation purposes". Punctuation and Latin pass through.
_CJK_RE = re.compile(r'[一-鿿]')


def _is_cjk(ch):
    return bool(_CJK_RE.match(ch))


def _word_level(word):
    """Return the HSK level (1-6) for a word, or None if not in HSK list.
    Caller decides whether unknown == "treat as advanced" (default) or
    "skip annotation" (rare; would only suit very strong students)."""
    return _hsk_map().get(word)


def _word_meets_threshold(word, threshold, annotate_unknown):
    """Decide whether a word should be annotated under the given threshold.

    Rules in order:
      1. If the word IS in the HSK list, use its level directly.
      2. Else, if EVERY character in the word is in HSK and below threshold,
         treat as easy and skip annotation. Catches common compounds the
         HSK list happens not to enumerate (e.g. 下次, 试试) where every
         component is itself easy.
      3. Otherwise, fall back to `annotate_unknown` — default True so
         genuinely unfamiliar terms (rare characters, domain vocabulary)
         do get pinyin.
    """
    level = _hsk_map().get(word)
    if level is not None:
        return level >= threshold
    char_levels = [_hsk_map().get(c) for c in word]
    if char_levels and all(l is not None and l < threshold for l in char_levels):
        return False
    return annotate_unknown


def _toned_pinyin_for(word):
    """Pinyin string for a word, with tone marks. Multi-syllable words
    join with no separator (e.g. 句子 → 'jùzi'), matching textbook
    convention. Drops the tone-5 (neutral) glyph since pypinyin already
    represents it as bare letters."""
    _, (pinyin_fn, Style) = _ensure_libs()
    syllables = pinyin_fn(word, style=Style.TONE, errors='ignore')
    # syllables is list[list[str]]: one inner list per character, normally
    # length 1 each. Flatten and join.
    flat = [s[0] for s in syllables if s]
    return ''.join(flat)


def _ruby(word, py):
    """Emit a single <ruby> tag. Both word and pinyin are escaped because
    they originate from AI output and may contain stray angle brackets."""
    return '<ruby>' + html.escape(word) + '<rt>' + html.escape(py) + '</rt></ruby>'


def annotate(text, mode='vocab', hsk_threshold=4, annotate_unknown=True):
    """Annotate CJK runs in `text` with ruby pinyin per the chosen mode.

    Args:
        text: feedback string. Non-CJK parts (Latin, digits, punctuation,
              math wrapped in $...$) are HTML-escaped and pass through.
        mode: 'off' (return text unchanged), 'vocab' (HSK threshold-based),
              or 'full' (every CJK char).
        hsk_threshold: in 'vocab' mode, words at this level or above are
                       annotated. Default 4 (HSK 4+).
        annotate_unknown: in 'vocab' mode, treat off-list words as
                          ≥ threshold (i.e. annotate them). Default True
                          since most off-list words in marking feedback
                          are advanced or domain-specific terms.

    Returns:
        HTML string safe to drop into existing feedback rendering.
    """
    if not text:
        return ''
    if mode == 'off':
        return html.escape(text)
    if mode not in ('vocab', 'full'):
        raise ValueError("mode must be 'off', 'vocab', or 'full'")

    jieba, _ = _ensure_libs()
    out = []

    # Walk the string, batching consecutive CJK chars into a single
    # segmenter call (so jieba sees natural word boundaries) and
    # passing non-CJK chunks through escaped.
    i, n = 0, len(text)
    buf_cjk = []

    def flush_cjk():
        if not buf_cjk:
            return
        chunk = ''.join(buf_cjk)
        buf_cjk.clear()
        if mode == 'full':
            # Per-character annotation regardless of HSK level.
            for ch in chunk:
                out.append(_ruby(ch, _toned_pinyin_for(ch)))
            return
        # 'vocab' mode: segment, look up HSK level, annotate if ≥ threshold.
        for word in jieba.cut(chunk, HMM=True):
            if not word:
                continue
            # Skip annotation for non-CJK fragments inside the chunk
            # (shouldn't happen since chunk is all CJK, but defensive).
            if not all(_is_cjk(c) for c in word):
                out.append(html.escape(word))
                continue
            if _word_meets_threshold(word, hsk_threshold, annotate_unknown):
                out.append(_ruby(word, _toned_pinyin_for(word)))
            else:
                out.append(html.escape(word))

    while i < n:
        ch = text[i]
        if _is_cjk(ch):
            buf_cjk.append(ch)
        else:
            flush_cjk()
            out.append(html.escape(ch))
        i += 1
    flush_cjk()
    return ''.join(out)


def annotate_dict(d, mode='vocab', fields=None, **kwargs):
    """Walk a dict (and nested lists/dicts) and REPLACE the given string
    fields with their annotated HTML. Mutates in place. Useful when
    post-processing the AI's parsed JSON result.
    """
    if fields is None:
        fields = {
            'well_done', 'main_gap', 'overall_feedback',
            'feedback', 'improvement', 'idea', 'correction_prompt',
            'student_answer', 'correct_answer',
        }
    fields = set(fields)

    def walk(node):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if k in fields and isinstance(v, str):
                    node[k] = annotate(v, mode=mode, **kwargs)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(d)
    return d


# Fields on the AI marking result that should be annotated. Each entry maps
# the raw text field -> the parallel HTML field name templates will render.
RESULT_TEXT_FIELDS = (
    'well_done', 'main_gap', 'overall_feedback',
)
RESULT_QUESTION_FIELDS = (
    'feedback', 'improvement', 'idea', 'correction_prompt',
    'student_answer', 'correct_answer',
)


def annotate_result_for_pinyin(result, mode, hsk_threshold=4):
    """Add `_html` siblings to selected string fields on the AI marking
    result so templates can render annotated ruby HTML while the raw
    Chinese stays available for editing, regen, and back-compat.

    Mutates `result` in place. Safe to call on a result that's already
    been annotated — re-derives _html from the raw fields each time, so
    a teacher edit followed by re-annotate produces the latest HTML.
    """
    if not isinstance(result, dict) or mode == 'off':
        return result

    for f in RESULT_TEXT_FIELDS:
        v = result.get(f)
        if isinstance(v, str) and v.strip():
            result[f + '_html'] = annotate(v, mode=mode, hsk_threshold=hsk_threshold)

    for q in (result.get('questions') or []):
        if not isinstance(q, dict):
            continue
        for f in RESULT_QUESTION_FIELDS:
            v = q.get(f)
            if isinstance(v, str) and v.strip():
                q[f + '_html'] = annotate(v, mode=mode, hsk_threshold=hsk_threshold)

    # 'recommended_actions' is a list of strings, not nested under a key.
    actions = result.get('recommended_actions')
    if isinstance(actions, list):
        result['recommended_actions_html'] = [
            annotate(a, mode=mode, hsk_threshold=hsk_threshold) if isinstance(a, str) else a
            for a in actions
        ]

    return result
