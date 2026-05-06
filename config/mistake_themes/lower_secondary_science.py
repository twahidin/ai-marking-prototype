# config/mistake_themes/lower_secondary_science.py
#
# 4 skills-based mistake categories for Lower Secondary Science (Sec 1–2).
# Skills, NOT topics — apply across measurement, energy, cells, matter,
# forces, and the rest of the LSS spiral.

THEMES = {

    "terminology_precision": {
        "label": "Terminology and Keywords",
        "description": "Right idea conveyed but the precise scientific term is missing or used loosely — wrote everyday language instead of the named concept (e.g. 'gets bigger' instead of 'expands').",
        "never_group": False,
    },

    "process_explanation": {
        "label": "Process and Mechanism",
        "description": "Student named a process but did not explain it correctly — missing or out-of-sequence steps, missing the underlying cause, or describing a mechanism that does not match the named process.",
        "never_group": False,
    },

    "reasoning_gap": {
        "label": "Reasoning Gap",
        "description": "Student stated a fact or observation but did not link it to the consequence the question asked about — missing cause→effect, or stopping short of the conclusion.",
        "never_group": False,
    },

    "content_misconception": {
        "label": "Content Misconception",
        "description": "Student brought the wrong concept to the question — confused two ideas (mass vs weight, evaporation vs boiling), or held a fundamentally incorrect mental model.",
        "never_group": False,
    },
}
