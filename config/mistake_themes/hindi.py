# mistake_themes/hindi.py
#
# Mistake themes for Hindi (O Level / N Level)
# Singapore syllabus
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    "hindi_lifting": {
        "label": "Lifted From Passage",
        "description": "Student copied phrases directly from the passage instead of paraphrasing in own words.",
        "never_group": False,
    },

    "hindi_inference_not_made": {
        "label": "Inference Not Made",
        "description": "Student answered at a surface level when the question required drawing an implication from the passage.",
        "never_group": False,
    },

    "hindi_register_wrong": {
        "label": "Register Wrong",
        "description": "Student used informal or spoken Hindi in a formal writing task — did not use standard written Hindi (Manak Hindi) as required.",
        "never_group": False,
    },

    "hindi_gender_agreement_wrong": {
        "label": "Gender Agreement Error",
        "description": "Student used incorrect grammatical gender for a noun, causing disagreement with the verb or adjective — common for nouns whose gender is not obvious.",
        "never_group": False,
    },

    "hindi_spelling_error": {
        "label": "Spelling Error",
        "description": "Student misspelled a Hindi word — common errors include incorrect use of matra (vowel diacritics) or confusion between similar Devanagari characters.",
        "never_group": False,
    },

    "hindi_composition_no_structure": {
        "label": "Composition Lacks Structure",
        "description": "Composition does not have a clear introduction (भूमिका), body (विस्तार), and conclusion (उपसंहार).",
        "never_group": False,
    },

    "hindi_muhavara_wrong": {
        "label": "Muhavara Used Incorrectly",
        "description": "Student used a muhavara (idiom) or lokokti (proverb) in the wrong context, or its meaning does not fit the point being made.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # ==================================================================
}
