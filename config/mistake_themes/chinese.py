# mistake_themes/chinese.py
#
# Mistake themes for Chinese Language / Higher Chinese (O Level / N Level)
# Singapore syllabus — covers comprehension, summary, composition,
# and language use components
#
# NOTE: Category labels and descriptions are intentionally written in
# English here so they work in the AI prompt. The feedback shown to
# students is already handled by the language directive in ai.py which
# forces all student-facing text into Chinese.
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # COMPREHENSION (阅读理解)
    # ==================================================================

    "cl_lifting": {
        "label": "Lifted From Passage",
        "description": "Student copied phrases or sentences directly from the passage without paraphrasing — answer was not in own words as required.",
        "never_group": False,
    },

    "cl_inference_not_made": {
        "label": "Inference Not Made",
        "description": "Student answered at a surface level when the question required drawing a conclusion or implication from the passage.",
        "never_group": False,
    },

    "cl_wrong_section_used": {
        "label": "Wrong Part of Passage Used",
        "description": "Student based their answer on the wrong paragraph — answer is plausible but not supported by the specified lines.",
        "never_group": False,
    },

    # ==================================================================
    # LANGUAGE USE (语言运用)
    # ==================================================================

    "cl_homophone_error": {
        "label": "Homophone Character Error",
        "description": "Student used a character that sounds like the correct one but has a different meaning — e.g. 再 vs 在, 做 vs 作.",
        "never_group": False,
    },

    "cl_wrong_character": {
        "label": "Wrong Character Written",
        "description": "Student wrote a character that is visually similar to the correct one but is semantically incorrect — a stroke or component error changing the meaning.",
        "never_group": False,
    },

    "cl_chengyu_wrong": {
        "label": "Chengyu Used Incorrectly",
        "description": "Student used a chengyu (成语) in the wrong context — meaning does not fit the sentence, or a chengyu was used that has a negative connotation in a positive context.",
        "never_group": False,
    },

    "cl_sentence_connector_missing": {
        "label": "Sentence Connector Missing",
        "description": "Student did not use connective words or phrases to link ideas — e.g. missing 因为…所以, 虽然…但是, or 首先…其次 where they are needed.",
        "never_group": False,
    },

    "cl_register_wrong": {
        "label": "Register Wrong",
        "description": "Student used overly colloquial or informal language in a formal writing task, or used stilted formal language in an informal context.",
        "never_group": False,
    },

    # ==================================================================
    # COMPOSITION (作文)
    # ==================================================================

    "cl_composition_no_structure": {
        "label": "Composition Lacks Structure",
        "description": "Composition does not have a clear opening, development, and conclusion — ideas are presented without logical organisation.",
        "never_group": False,
    },

    "cl_composition_plot_only": {
        "label": "Plot Without Reflection",
        "description": "Student narrated events in sequence but did not include personal reflection, emotion, or insight — reads as a plot summary rather than a personal piece.",
        "never_group": False,
    },

    "cl_composition_off_topic": {
        "label": "Off Topic",
        "description": "Composition drifted from the given title or prompt — student wrote about a related but different topic.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
