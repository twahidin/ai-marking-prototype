"""Level resolution for the department insights dashboard.

`Class.level` is a free-text string the teacher / HOD types in (e.g. "Sec 1",
"1E5", "Secondary 4 Express"). The dept insights page groups data by level —
Sec 1 / 2 / 3 / 4-5 — and switches data per level tab. This module is the
single source of truth for that normalisation so every caller agrees.

In Singapore secondary school context, Sec 1–5 are *levels*. "Band" refers
to G1/G2/G3 streaming (post-PSLE Subject-Based Banding) which is a separate
axis not yet modelled here — do not conflate the two.

Sec 4 and Sec 5 collapse to one level per the v1 brief (similar syllabuses).
Anything we can't classify goes to 'unlevelled' — included silently in the
'All' tab; the dashboard surfaces a pseudo-tab only when at least one class
resolves there.
"""

from __future__ import annotations

import re

LEVEL_SEC1 = 'sec1'
LEVEL_SEC2 = 'sec2'
LEVEL_SEC3 = 'sec3'
LEVEL_SEC45 = 'sec45'
LEVEL_UNLEVELLED = 'unlevelled'
LEVEL_ALL = 'all'

LEVEL_LABELS = {
    LEVEL_SEC1: 'Sec 1',
    LEVEL_SEC2: 'Sec 2',
    LEVEL_SEC3: 'Sec 3',
    LEVEL_SEC45: 'Sec 4/5',
    LEVEL_UNLEVELLED: 'Unlevelled',
    LEVEL_ALL: 'All',
}

ORDERED_LEVEL_KEYS = (LEVEL_SEC1, LEVEL_SEC2, LEVEL_SEC3, LEVEL_SEC45)

# Match the level *token* — after stripping "sec/secondary" we expect the
# string to START with a digit 1-5 (possibly followed by letters or another
# digit, e.g. "1A", "1E5", "4N"). This deliberately rejects "JC1", "Pri 6",
# "Year 5 IB" etc. because their leading token isn't a sec-1-to-5 digit.
_SEC_PREFIX_RE = re.compile(r"^(secondary|sec|s)\.?\s*", re.IGNORECASE)


def resolve_level(level):
    """Map a free-text level into a level key.

    Examples:
      'sec 1', '1', '1A', 'Secondary 1', '1E5' → 'sec1'
      'sec 4', '5', '4/5', '4N', '5T'           → 'sec45'
      None, '', 'JC1', 'Pri 6'                  → 'unlevelled'
    """
    if not level:
        return LEVEL_UNLEVELLED
    text = str(level).strip().lower()
    if not text:
        return LEVEL_UNLEVELLED

    # "sec 4/5" / "4/5" / "sec 4-5" — collapse to sec45 outright.
    if re.search(r"\b4\s*[/\-]\s*5\b", text):
        return LEVEL_SEC45

    # Strip any "sec/secondary/s" prefix, then expect a leading 1-5 digit.
    stripped = _SEC_PREFIX_RE.sub("", text)
    if not stripped:
        return LEVEL_UNLEVELLED
    first = stripped[0]
    if first == '1':
        return LEVEL_SEC1
    if first == '2':
        return LEVEL_SEC2
    if first == '3':
        return LEVEL_SEC3
    if first in ('4', '5'):
        return LEVEL_SEC45
    return LEVEL_UNLEVELLED


def level_label(key):
    return LEVEL_LABELS.get(key, key.title())


def classes_in_level(classes, level):
    """Filter an iterable of `Class` rows to those resolving to `level`.

    `LEVEL_ALL` returns every class. `LEVEL_UNLEVELLED` returns only
    classes whose level can't be parsed. Other levels return their
    matching set.
    """
    if level == LEVEL_ALL:
        return list(classes)
    return [c for c in classes if resolve_level(c.level) == level]


def levels_present(classes):
    """Return the ordered subset of level keys that have at least one class.
    Adds 'unlevelled' only if any class can't be parsed."""
    present = set()
    saw_unlevelled = False
    for c in classes:
        lvl = resolve_level(c.level)
        if lvl == LEVEL_UNLEVELLED:
            saw_unlevelled = True
        else:
            present.add(lvl)
    out = [k for k in ORDERED_LEVEL_KEYS if k in present]
    if saw_unlevelled:
        out.append(LEVEL_UNLEVELLED)
    return out


# ---------------------------------------------------------------------------
# Back-compat aliases — the rest of the codebase still uses the old "band"
# names for the Sec-1-to-5 axis. These re-exports let callers migrate
# incrementally without one giant rename PR. Remove once the sweep is
# complete (`grep -rE '\bBAND_|resolve_band|classes_in_band|bands_present' .`
# returns empty across non-comment lines).
# ---------------------------------------------------------------------------
BAND_SEC1 = LEVEL_SEC1
BAND_SEC2 = LEVEL_SEC2
BAND_SEC3 = LEVEL_SEC3
BAND_SEC45 = LEVEL_SEC45
BAND_UNBANDED = LEVEL_UNLEVELLED
BAND_ALL = LEVEL_ALL
BAND_LABELS = LEVEL_LABELS
ORDERED_BAND_KEYS = ORDERED_LEVEL_KEYS
resolve_band = resolve_level
band_label = level_label
classes_in_band = classes_in_level
bands_present = levels_present
