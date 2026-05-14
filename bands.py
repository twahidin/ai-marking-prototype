"""Level-band resolution for the department insights dashboard.

`Class.level` is a free-text string the teacher / HOD types in (e.g. "Sec 1",
"1E5", "Secondary 4 Express"). The dept insights page groups data by level
band — Sec 1 / 2 / 3 / 4-5 — and switches data per band tab. This module is
the single source of truth for that normalisation so every caller agrees.

Sec 4 and Sec 5 collapse to one band per the v1 brief (similar syllabuses).
Anything we can't classify goes to 'unbanded' — included silently in the
'All' tab; the dashboard surfaces a pseudo-tab only when at least one class
resolves there.
"""

from __future__ import annotations

import re

BAND_SEC1 = 'sec1'
BAND_SEC2 = 'sec2'
BAND_SEC3 = 'sec3'
BAND_SEC45 = 'sec45'
BAND_UNBANDED = 'unbanded'
BAND_ALL = 'all'

BAND_LABELS = {
    BAND_SEC1: 'Sec 1',
    BAND_SEC2: 'Sec 2',
    BAND_SEC3: 'Sec 3',
    BAND_SEC45: 'Sec 4/5',
    BAND_UNBANDED: 'Unbanded',
    BAND_ALL: 'All',
}

ORDERED_BAND_KEYS = (BAND_SEC1, BAND_SEC2, BAND_SEC3, BAND_SEC45)

# Match the level *token* — after stripping "sec/secondary" we expect the
# string to START with a digit 1-5 (possibly followed by letters or another
# digit, e.g. "1A", "1E5", "4N"). This deliberately rejects "JC1", "Pri 6",
# "Year 5 IB" etc. because their leading token isn't a sec-1-to-5 digit.
_SEC_PREFIX_RE = re.compile(r"^(secondary|sec|s)\.?\s*", re.IGNORECASE)


def resolve_band(level):
    """Map a free-text level into a band key.

    Examples:
      'sec 1', '1', '1A', 'Secondary 1', '1E5' → 'sec1'
      'sec 4', '5', '4/5', '4N', '5T'           → 'sec45'
      None, '', 'JC1', 'Pri 6'                  → 'unbanded'
    """
    if not level:
        return BAND_UNBANDED
    text = str(level).strip().lower()
    if not text:
        return BAND_UNBANDED

    # "sec 4/5" / "4/5" / "sec 4-5" — collapse to sec45 outright.
    if re.search(r"\b4\s*[/\-]\s*5\b", text):
        return BAND_SEC45

    # Strip any "sec/secondary/s" prefix, then expect a leading 1-5 digit.
    stripped = _SEC_PREFIX_RE.sub("", text)
    if not stripped:
        return BAND_UNBANDED
    first = stripped[0]
    if first == '1':
        return BAND_SEC1
    if first == '2':
        return BAND_SEC2
    if first == '3':
        return BAND_SEC3
    if first in ('4', '5'):
        return BAND_SEC45
    return BAND_UNBANDED


def band_label(band):
    return BAND_LABELS.get(band, band.title())


def classes_in_band(classes, band):
    """Filter an iterable of `Class` rows to those resolving to `band`.

    `BAND_ALL` returns every class. `BAND_UNBANDED` returns only classes
    whose level can't be parsed. Other bands return their matching set.
    """
    if band == BAND_ALL:
        return list(classes)
    return [c for c in classes if resolve_band(c.level) == band]


def bands_present(classes):
    """Return the ordered subset of band keys that have at least one class.
    Adds 'unbanded' only if any class can't be parsed."""
    present = set()
    saw_unbanded = False
    for c in classes:
        b = resolve_band(c.level)
        if b == BAND_UNBANDED:
            saw_unbanded = True
        else:
            present.add(b)
    out = [k for k in ORDERED_BAND_KEYS if k in present]
    if saw_unbanded:
        out.append(BAND_UNBANDED)
    return out
