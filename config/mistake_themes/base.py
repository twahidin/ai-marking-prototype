# mistake_themes/base.py
#
# Universal mistake themes shared across ALL subjects.
# These are broad categories that apply regardless of subject.
# Subject-specific files extend these with more precise, syllabus-aligned categories.
#
# STRUCTURE OF EACH THEME:
#   "key": {
#       "label":       Short display name shown to students and teachers (3-5 words)
#       "description": One sentence describing this error type — used in the AI prompt
#       "never_group": If True, this theme is never grouped with others in the summary
#                      (use for one-off errors that don't form patterns)
#   }

THEMES = {

    # ------------------------------------------------------------------
    # CARELESS / PROCEDURAL
    # ------------------------------------------------------------------
    "careless_slip": {
        "label": "Careless Slip",
        "description": "Student understood the concept but made a minor error in execution — arithmetic mistake, copied wrong value, or missed a step they clearly know.",
        "never_group": False,
    },

    "incomplete_answer": {
        "label": "Incomplete Answer",
        "description": "Student started correctly but did not finish — answer is cut off, missing final step, or missing a required part of the response.",
        "never_group": False,
    },

    "misread_question": {
        "label": "Misread Question",
        "description": "Student answered a different question from what was asked — misread a keyword, ignored a qualifier, or addressed the wrong aspect of the question.",
        "never_group": False,
    },

    "working_not_shown": {
        "label": "Working Not Shown",
        "description": "Correct or near-correct answer given but method or reasoning not shown, losing method marks.",
        "never_group": False,
    },

    # ------------------------------------------------------------------
    # CONTENT / KNOWLEDGE
    # ------------------------------------------------------------------
    "content_gap": {
        "label": "Content Gap",
        "description": "Student lacked the required knowledge — wrong fact, missing concept, or used an incorrect definition.",
        "never_group": False,
    },

    "keyword_missing": {
        "label": "Missing Keyword",
        "description": "Student conveyed the right idea but did not use the required technical term or key phrase that earns the mark.",
        "never_group": False,
    },

    "misconception": {
        "label": "Misconception",
        "description": "Student held a fundamentally incorrect understanding of a concept — not a knowledge gap but an actively wrong mental model.",
        "never_group": False,
    },

    # ------------------------------------------------------------------
    # EXPRESSION / COMMUNICATION
    # ------------------------------------------------------------------
    "too_vague": {
        "label": "Too Vague",
        "description": "Answer was correct in direction but too general — lacked the specificity, detail, or precision required to earn the mark.",
        "never_group": False,
    },

    "language_error": {
        "label": "Language Error",
        "description": "Grammatical, spelling, or punctuation error that affected clarity or lost marks in language-assessed components.",
        "never_group": False,
    },

    # ------------------------------------------------------------------
    # EXAM TECHNIQUE
    # ------------------------------------------------------------------
    "mark_allocation_ignored": {
        "label": "Mark Allocation Ignored",
        "description": "Student gave a one-line answer to a question worth 3+ marks, or wrote an essay for a 1-mark question — answer length did not match marks available.",
        "never_group": False,
    },

    "question_format_not_followed": {
        "label": "Format Not Followed",
        "description": "Student did not follow a required format — e.g. did not write in full sentences when required, did not use a table, did not label a diagram.",
        "never_group": False,
    },
}
