"""Canonical subject taxonomy.

Single source of truth for the assignment-creation dropdown / autocomplete.
The Assignment.subject column stores the human-readable display string
chosen from this list (e.g. 'Physics', 'Higher Chinese'); calibration
retrieval, marking-principles caching, and categorisation corrections all
match on that string (case-insensitive) — no separate slug / family
column is persisted.

Each entry has:
  - key:         slugged identifier (used by the dropdown JS)
  - display:     human-readable label shown in UI / dropdown / patterns page
                 — also what gets written to Assignment.subject
  - aliases:     freeform strings teachers commonly type that should resolve
                 to this entry's `key` (used by the autocomplete fallback)

The taxonomy is intentionally Singapore-secondary-school shaped. Future
band-level subdivision (G1 / G2 / G3) is anticipated but deliberately
NOT modelled here yet — when it ships, add a separate `band` column on
Assignment. Per the schema-evolution policy in CLAUDE.md, we don't add
NULL-everywhere columns ahead of time.
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
    {'key': 'computer_applications',   'display': 'Computer Applications',
     'aliases': ['computer applications', 'ca', 'cpa', 'comp app', 'comp apps',
                 'computer application']},
    {'key': 'computing',               'display': 'Computing',
     'aliases': ['computing', 'computer science', 'cs', 'comp']},
    {'key': 'design_and_technology',   'display': 'Design and Technology',
     'aliases': ['d&t', 'dnt', 'design technology',
                 'design & technology', 'design and technology']},
    {'key': 'english',                 'display': 'English',
     'aliases': ['english', 'el', 'english language']},
    {'key': 'geography',               'display': 'Geography',
     'aliases': ['geography', 'geog']},
    {'key': 'history',                 'display': 'History',
     'aliases': ['history', 'hist']},
    {'key': 'literature_in_english',   'display': 'Literature in English',
     'aliases': ['literature in english', 'literature', 'lit',
                 'english literature']},
    {'key': 'lower_secondary_science', 'display': 'Lower Secondary Science',
     'aliases': ['lower secondary science', 'lower sec science', 'lss']},
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
    {'key': 'principles_of_accounts',  'display': 'Principles of Accounts',
     'aliases': ['poa', 'principles of accounts', 'accounts', 'accounting']},
    {'key': 'science',                 'display': 'Science',
     'aliases': ['science', 'sci']},
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


def resolve_subject_key(text):
    """Resolve a freeform subject string to a canonical key.

    Tries exact display match → alias match → None. Returns None if no
    confident match; callers can fall back to keeping the freeform
    string (it just won't share calibration with canonical-subject rows).
    """
    if not text:
        return None
    s = text.strip().lower()
    if s in DISPLAY_TO_KEY:
        return DISPLAY_TO_KEY[s]
    if s in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[s]
    return None


def canonicalise_subject(text):
    """Normalise a freeform subject string to its canonical display form
    if it resolves to a taxonomy entry; otherwise return the input
    unchanged (just whitespace-stripped). Call at every write site so
    aliases like 'maths', 'hcl', 'phy' all collapse to the canonical
    display string ('Mathematics', 'Chinese', 'Physics') in the DB —
    that's what the cross-assignment retrieval / principles cache /
    marking-patterns page key on. Freeform input ('Sec 3 Maths') falls
    through unchanged so the freeform-isolation gate still picks it up.
    """
    if text is None:
        return ''
    stripped = str(text).strip()
    if not stripped:
        return ''
    key = resolve_subject_key(stripped)
    if key:
        return display_name(key)
    return stripped


def is_canonical_subject(text):
    """True if the freeform subject string maps to a canonical taxonomy
    entry (display match or alias hit). Used to gate cross-assignment
    behaviour: only canonical-subject assignments contribute to the
    shared calibration corpus, marking principles, and categorisation
    corrections. Freeform subjects are treated as one-off, intra-
    assignment-only — their feedback edits still propagate within the
    same assignment but never reach a different assignment's marking.
    """
    return resolve_subject_key(text) is not None


def display_name(key):
    """Human-readable label for a family key. Falls back to the key
    titlecased if the key is unknown (e.g. a stale legacy value)."""
    if not key:
        return ''
    return KEY_TO_DISPLAY.get(key, key.replace('_', ' ').title())
