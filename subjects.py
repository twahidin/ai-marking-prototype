"""Canonical subject taxonomy.

Single source of truth for:
  - the dropdown / autocomplete on the assignment creation form
  - the AI classifier in ai_marking.classify_subject_family
  - the marking-patterns display name
  - the calibration / propagation lookup keys (FeedbackEdit.subject_family)

Each entry has:
  - key:         slugged identifier persisted in DB columns
  - display:     human-readable label shown in UI / dropdown / patterns page
  - aliases:     freeform strings teachers commonly type that should resolve
                 to this family without an AI round-trip

The taxonomy is intentionally Singapore-secondary-school shaped. Future
band-level subdivision (G1 / G2 / G3) is anticipated but deliberately
NOT modelled here yet — when it ships, add a separate `band` column on
Assignment and FeedbackEdit. Per the schema-evolution policy in
CLAUDE.md, we don't add NULL-everywhere columns ahead of time.
"""

# Order: alphabetical by display name. Same order is used in the
# dropdown UI, the AI classifier prompt, and the marking-patterns page —
# all derived from this list, so reordering here propagates everywhere.
SUBJECTS = [
    {'key': 'art',                     'display': 'Art',
     'aliases': ['art', 'visual art']},
    {'key': 'biology',                 'display': 'Biology',
     'aliases': ['biology', 'bio']},
    {'key': 'chemistry',               'display': 'Chemistry',
     'aliases': ['chemistry', 'chem']},
    {'key': 'chinese',                 'display': 'Chinese',
     'aliases': ['chinese', 'higher chinese', 'chinese language', 'cl', 'hcl']},
    {'key': 'english',                 'display': 'English',
     'aliases': ['english', 'el', 'english language']},
    {'key': 'geography',               'display': 'Geography',
     'aliases': ['geography', 'geog']},
    {'key': 'hindi',                   'display': 'Hindi',
     'aliases': ['hindi']},
    {'key': 'history',                 'display': 'History',
     'aliases': ['history', 'hist']},
    {'key': 'literature_in_english',   'display': 'Literature in English',
     'aliases': ['literature in english', 'literature', 'lit',
                 'english literature']},
    {'key': 'lower_secondary_science', 'display': 'Lower Secondary Science',
     'aliases': ['lower secondary science', 'lower sec science', 'lss',
                 'science', 'general science']},
    {'key': 'malay',                   'display': 'Malay',
     'aliases': ['malay', 'higher malay', 'bahasa melayu']},
    {'key': 'mathematics',             'display': 'Mathematics',
     'aliases': ['mathematics', 'math', 'maths', 'a math', 'a maths',
                 'e math', 'e maths', 'additional mathematics',
                 'elementary mathematics']},
    {'key': 'music',                   'display': 'Music',
     'aliases': ['music']},
    {'key': 'nutrition_and_food_science', 'display': 'Nutrition and Food Science',
     'aliases': ['nutrition and food science', 'nfs', 'food science',
                 'food and nutrition', 'home economics']},
    {'key': 'physics',                 'display': 'Physics',
     'aliases': ['physics', 'phy', 'phys']},
    {'key': 'social_studies',          'display': 'Social Studies',
     'aliases': ['social studies', 'ss']},
    {'key': 'tamil',                   'display': 'Tamil',
     'aliases': ['tamil', 'higher tamil', 'tamil language']},
]

# Cached lookups built once at import time.
SUBJECT_KEYS = [s['key'] for s in SUBJECTS]
SUBJECT_DISPLAY_NAMES = [s['display'] for s in SUBJECTS]
KEY_TO_DISPLAY = {s['key']: s['display'] for s in SUBJECTS}
DISPLAY_TO_KEY = {s['display'].lower(): s['key'] for s in SUBJECTS}

# Alias → key. Prebuilt for O(1) lookup in the keyword shortcut path.
_ALIAS_TO_KEY = {}
for _s in SUBJECTS:
    for _alias in _s.get('aliases', []):
        _ALIAS_TO_KEY[_alias.lower().strip()] = _s['key']


# Old 7-family taxonomy. Kept here only so the migration can detect
# rows that need re-classification under the new taxonomy. Do NOT use
# these keys for any new code — the canonical set is SUBJECT_KEYS.
LEGACY_FAMILY_KEYS = {
    'science', 'humanities_seq', 'humanities_sbq', 'literature',
    'mother_tongue_comprehension', 'mother_tongue_composition',
    'mother_tongue_translation',
}


def resolve_subject_key(text):
    """Resolve a freeform subject string to a canonical family key.

    Tries exact display match → alias match → None. Returns None if no
    confident match; callers fall back to the AI classifier.
    """
    if not text:
        return None
    s = text.strip().lower()
    if s in DISPLAY_TO_KEY:
        return DISPLAY_TO_KEY[s]
    if s in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[s]
    return None


def display_name(key):
    """Human-readable label for a family key. Falls back to the key
    titlecased if the key is unknown (e.g. a stale legacy value)."""
    if not key:
        return ''
    return KEY_TO_DISPLAY.get(key, key.replace('_', ' ').title())
