# mistake_themes/art.py
#
# Mistake themes for Art (O Level / N Level)
# Singapore syllabus — covers written paper (art history, visual analysis)
# and practical components
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    "art_element_named_not_analysed": {
        "label": "Element Named, Not Analysed",
        "description": "Student identified an element of art or principle of design (e.g. line, colour, balance) but did not explain how it contributes to the work's meaning or effect.",
        "never_group": False,
    },

    "art_description_not_analysis": {
        "label": "Described Instead of Analysed",
        "description": "Student described what they see in the artwork (colour, shape, subject matter) without analysing why the artist made those choices.",
        "never_group": False,
    },

    "art_context_not_used": {
        "label": "Historical Context Not Used",
        "description": "Student analysed the artwork in isolation without referencing the historical, cultural, or social context that shaped it.",
        "never_group": False,
    },

    "art_artist_intent_missing": {
        "label": "Artist Intent Not Addressed",
        "description": "Student described the artwork but did not consider what the artist was trying to communicate, express, or achieve.",
        "never_group": False,
    },

    "art_terminology_wrong": {
        "label": "Art Terminology Wrong",
        "description": "Student used an art term incorrectly — e.g. confused 'tone' with 'colour', or 'texture' with 'pattern'.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # ==================================================================
}
