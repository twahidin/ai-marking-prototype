# mistake_themes/malay.py
#
# Mistake themes for Malay Language / Higher Malay (O Level / N Level)
# Singapore syllabus
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    "malay_lifting": {
        "label": "Lifted From Passage",
        "description": "Student copied phrases directly from the passage instead of paraphrasing in own words.",
        "never_group": False,
    },

    "malay_inference_not_made": {
        "label": "Inference Not Made",
        "description": "Student answered at a surface level when the question required drawing an implication or conclusion from the passage.",
        "never_group": False,
    },

    "malay_register_wrong": {
        "label": "Register Wrong",
        "description": "Student used informal Bahasa Melayu (including Singlish-influenced Malay or colloquial terms) in a formal writing task.",
        "never_group": False,
    },

    "malay_spelling_error": {
        "label": "Spelling Error",
        "description": "Student misspelled a Malay word — common errors include confusion between similar-sounding words or incorrect application of spelling rules.",
        "never_group": False,
    },

    "malay_sentence_structure_wrong": {
        "label": "Sentence Structure Wrong",
        "description": "Student constructed a grammatically incorrect sentence — common errors in word order, verb agreement, or use of affixes (awalan/akhiran).",
        "never_group": False,
    },

    "malay_composition_no_structure": {
        "label": "Composition Lacks Structure",
        "description": "Composition does not have a clear pendahuluan (introduction), isi (body), and penutup (conclusion).",
        "never_group": False,
    },

    "malay_peribahasa_wrong": {
        "label": "Peribahasa Used Incorrectly",
        "description": "Student used a peribahasa (proverb) in the wrong context, or its meaning does not fit the point being made.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # ==================================================================
}
