# config/mistake_themes/geography.py
#
# 4 skills-based mistake categories for Geography. Skills, NOT specific
# topics — apply equally to physical geography, human geography, and
# fieldwork / data-response questions.

THEMES = {

    "case_study_application": {
        "label": "Case Study and Example Use",
        "description": "Case study or named example missing, generic, or not applied — answer relies on textbook generalisations instead of locating evidence in a specific named place, or the named example is mentioned without details that make it relevant.",
        "never_group": False,
    },

    "process_explanation": {
        "label": "Process and Mechanism",
        "description": "Geographical process named but not explained — missing or out-of-sequence steps in a physical/human process (e.g. erosion, urbanisation), or describing a mechanism that doesn't match the named process.",
        "never_group": False,
    },

    "reasoning_gap": {
        "label": "Reasoning Gap",
        "description": "Student stated a factor or observation but did not link it to the consequence — missing cause→effect, missing the mechanism connecting them, or stopping short of the impact the question asked about.",
        "never_group": False,
    },

    "terminology_precision": {
        "label": "Terminology and Keywords",
        "description": "Right idea conveyed but the precise geographical term is missing or used loosely — wrote everyday language where the syllabus term was expected (e.g. 'rich country' instead of 'developed economy').",
        "never_group": False,
    },
}
