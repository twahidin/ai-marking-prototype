# config/mistake_themes/__init__.py
#
# Entry point for the mistake themes system.
#
# Public API:
#   THEMES               — base (universal) themes; fallback for freeform /
#                          unknown subjects and the legacy global import path
#                          `from config.mistake_themes import THEMES`.
#   themes_for(subject)  — pass any subject string (canonical display, alias,
#                          or freeform); returns the merged base + subject-
#                          specific dict.
#   get_themes_for_subject(subject_key) — lower-level: takes a canonical
#                          subject KEY (e.g. 'physics'), no string resolution.
#
# Per-subject files live alongside this __init__.py in the same package.
# Subject themes override base themes on key collision, so a subject file
# can sharpen a generic base description without redeclaring the whole entry.

import importlib
import logging

from config.mistake_themes.base import THEMES as _BASE_THEMES

logger = logging.getLogger(__name__)

# Re-export base themes as the package-level THEMES so legacy callers that do
# `from config.mistake_themes import THEMES` keep working — they get the base
# (subject-agnostic) set, which is the right fallback for non-subject-aware
# code paths.
THEMES = _BASE_THEMES

# Maps subject keys (from subjects.py SUBJECTS list) to their theme module name.
# Add a new entry here whenever you create a new subject file.
SUBJECT_MODULE_MAP = {
    'art':                        'config.mistake_themes.art',
    'biology':                    'config.mistake_themes.biology',
    'chemistry':                  'config.mistake_themes.chemistry',
    'chinese':                    'config.mistake_themes.chinese',
    'english':                    'config.mistake_themes.english',
    'geography':                  'config.mistake_themes.geography',
    'hindi':                      'config.mistake_themes.hindi',
    'history':                    'config.mistake_themes.history',
    'literature_in_english':      'config.mistake_themes.literature_in_english',
    'lower_secondary_science':    'config.mistake_themes.lower_secondary_science',
    'malay':                      'config.mistake_themes.malay',
    'mathematics':                'config.mistake_themes.mathematics',
    'music':                      'config.mistake_themes.music',
    'nutrition_and_food_science': 'config.mistake_themes.nutrition_and_food_science',
    'physics':                    'config.mistake_themes.physics',
    'social_studies':             'config.mistake_themes.social_studies',
    'tamil':                      'config.mistake_themes.tamil',
}


def get_themes_for_subject(subject_key):
    """Return merged dict of base themes + subject-specific themes.

    Subject themes override base themes when keys clash — this lets a
    subject file sharpen a generic base theme with a more specific
    description without duplicating the whole entry.

    Falls back to base themes only if:
    - subject_key is None or blank
    - no module exists for that subject
    - the subject module fails to import
    """
    if not subject_key:
        return dict(_BASE_THEMES)

    module_path = SUBJECT_MODULE_MAP.get(subject_key)
    if not module_path:
        logger.debug(f"No subject theme file for key '{subject_key}' — using base themes only")
        return dict(_BASE_THEMES)

    try:
        module = importlib.import_module(module_path)
        subject_themes = getattr(module, 'THEMES', {})
        return {**_BASE_THEMES, **subject_themes}
    except Exception as e:
        logger.warning(f"Could not load theme module '{module_path}': {e} — falling back to base themes")
        return dict(_BASE_THEMES)


def themes_for(subject):
    """Convenience wrapper: takes ANY subject string (canonical display
    name, alias, or freeform) and returns the merged theme dict.

    Resolves the string to a canonical subject key via subjects.py; falls
    back to base themes for freeform / unknown subjects.
    """
    from subjects import resolve_subject_key
    key = resolve_subject_key(subject) if subject else None
    return get_themes_for_subject(key)


# Deprecated keys from the previous (pre-per-subject) taxonomy. Kept ONLY
# so the renderer can show clean human labels for legacy `theme_key`
# values still living in old FeedbackEdit rows and old Submission
# result_json blobs. Never injected into the AI prompt and never offered
# in the teacher-correction dropdown — see themes_for_display below.
LEGACY_THEMES = {
    "reasoning_gap": {
        "label": "Reasoning gap (legacy)",
        "description": "Pre-2026 universal taxonomy — kept for label display only.",
        "never_group": False,
        "deprecated": True,
    },
    "evidence_handling": {
        "label": "Evidence handling (legacy)",
        "description": "Pre-2026 universal taxonomy — kept for label display only.",
        "never_group": False,
        "deprecated": True,
    },
    "language_expression": {
        "label": "Language and expression (legacy)",
        "description": "Pre-2026 universal taxonomy — kept for label display only.",
        "never_group": False,
        "deprecated": True,
    },
    "procedural_error": {
        "label": "Procedural error (legacy)",
        "description": "Pre-2026 universal taxonomy — kept for label display only.",
        "never_group": False,
        "deprecated": True,
    },
}


def themes_for_display(subject):
    """Display-side version of themes_for: includes deprecated legacy
    keys so the renderer can resolve labels for old submissions.

    Use this at READ paths only (feedback view route, grouping payload).
    Categorisation, AI prompts, and the teacher correction dropdown must
    keep using themes_for() so the new taxonomy stays clean.

    On collision, the active per-subject dict wins — a legacy key that's
    been re-introduced under the new taxonomy gets the current entry.
    """
    return {**LEGACY_THEMES, **themes_for(subject)}


def theme_keys():
    """Return the list of base theme keys in declaration order.

    Kept for legacy callers; subject-aware code should call
    `themes_for(subject).keys()` instead.
    """
    return list(_BASE_THEMES.keys())


def theme_label(key):
    """Return the display label for a base theme key, or the key itself
    if unknown. Kept for legacy callers."""
    return (_BASE_THEMES.get(key) or {}).get('label', key)


def is_never_group(key):
    """True if criteria with this base theme must always render standalone.
    Kept for legacy callers."""
    return bool((_BASE_THEMES.get(key) or {}).get('never_group'))
