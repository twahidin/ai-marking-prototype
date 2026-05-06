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
    """Return the theme dict for a canonical subject.

    When a subject file exists, its THEMES dict is returned VERBATIM — no
    merge with base. The deliberate design: each subject file owns the
    complete, slim list of skills-based categories that show up in the
    teacher's "Mistake Category" dropdown. Mixing in base entries would
    re-introduce the mental clutter the slim-down was meant to remove.

    Base themes are returned only as the fallback when:
    - subject_key is None or blank
    - no module exists for that subject (freeform / unrecognised)
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
        subject_themes = getattr(module, 'THEMES', None)
        if not subject_themes:
            return dict(_BASE_THEMES)
        return dict(subject_themes)
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
    # Pre-2026 universal-taxonomy keys.
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
    "content_gap": {
        "label": "Content gap (legacy)",
        "description": "Pre-2026 universal taxonomy — kept for label display only.",
        "never_group": False,
        "deprecated": True,
    },
    # Pre-slim-down per-subject keys (2026-05) — kept so old submissions
    # still resolve to a clean label rather than a raw snake_case key.
    # These are NEVER offered in the dropdown; themes_for() (strict) does
    # not include LEGACY_THEMES, so teachers can only choose from the
    # current 4 skills-based categories. Legacy keys appear only in
    # themes_for_display() at READ time.
    "careless_slip": {"label": "Careless slip (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "incomplete_answer": {"label": "Incomplete answer (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "misread_question": {"label": "Misread question (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "working_not_shown": {"label": "Working not shown (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "keyword_missing": {"label": "Missing keyword (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "misconception": {"label": "Misconception (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "too_vague": {"label": "Too vague (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "language_error": {"label": "Language error (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "mark_allocation_ignored": {"label": "Mark allocation ignored (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
    "question_format_not_followed": {"label": "Format not followed (legacy)", "description": "Pre-2026-05 base taxonomy.", "never_group": False, "deprecated": True},
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


def themes_meta_list(themes):
    """Serialise a themes dict as an ordered list of {key, label,
    description, never_group} entries. Used by JSON endpoints (e.g. the
    teacher feedback modal's category dropdown)."""
    return [
        {
            'key': k,
            'label': v.get('label', k),
            'description': v.get('description', ''),
            'never_group': bool(v.get('never_group')),
        } for k, v in themes.items()
    ]


def themes_meta_dict(themes):
    """Serialise a themes dict as a flat metadata map keyed by theme_key.
    Used by Jinja templates that inject FV_THEMES for client-side label
    fallback (e.g. the student feedback grouped view)."""
    return {
        k: {
            'label': v.get('label', k),
            'description': v.get('description', ''),
            'never_group': bool(v.get('never_group')),
        } for k, v in themes.items()
    }
