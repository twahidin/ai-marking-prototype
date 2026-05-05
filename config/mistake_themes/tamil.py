# mistake_themes/tamil.py
#
# Mistake themes for Tamil Language / Higher Tamil (O Level / N Level)
# Singapore syllabus
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    "tamil_lifting": {
        "label": "Lifted From Passage",
        "description": "Student copied phrases directly from the passage instead of paraphrasing in own words.",
        "never_group": False,
    },

    "tamil_inference_not_made": {
        "label": "Inference Not Made",
        "description": "Student answered at a surface level when the question required drawing an implication from the passage.",
        "never_group": False,
    },

    "tamil_register_wrong": {
        "label": "Register Wrong",
        "description": "Student used spoken/colloquial Tamil in a formal writing task — did not use written literary Tamil as required.",
        "never_group": False,
    },

    "tamil_spelling_error": {
        "label": "Spelling Error",
        "description": "Student misspelled a Tamil word — common errors include confusion between similar-looking characters or incorrect use of vowel markers (உயிர்மெய்).",
        "never_group": False,
    },

    "tamil_sandhi_error": {
        "label": "Sandhi Rule Error",
        "description": "Student made an error in applying Tamil sandhi rules — incorrect joining of words at boundaries, changing the sound or spelling of combined words.",
        "never_group": False,
    },

    "tamil_composition_no_structure": {
        "label": "Composition Lacks Structure",
        "description": "Composition does not have a clear introduction, development, and conclusion in proper Tamil essay form.",
        "never_group": False,
    },

    "tamil_thirukkural_wrong": {
        "label": "Thirukkural Used Incorrectly",
        "description": "Student cited a Thirukkural couplet but its meaning does not fit the context, or the couplet was misquoted.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # ==================================================================
}
