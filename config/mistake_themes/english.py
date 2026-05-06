# config/mistake_themes/english.py
#
# 4 skills-based mistake categories for English Language. Skills, NOT
# specific exam papers — apply equally to comprehension, situational
# writing, continuous writing, and visual text questions.

THEMES = {

    "comprehension": {
        "label": "Question Comprehension",
        "description": "Student misread the question or addressed only part of it — wrong focus, ignored a qualifier, missed the question's specific demand (e.g. 'in your own words', 'with reference to lines X–Y'), or wrote off-topic.",
        "never_group": False,
    },

    "evidence_use": {
        "label": "Evidence and Textual Reference",
        "description": "Evidence missing, wrong, or not unpacked — quote not provided where required, evidence chosen does not support the claim, or quote given without explaining what it shows.",
        "never_group": False,
    },

    "expression_precision": {
        "label": "Language and Expression",
        "description": "Imprecise, informal, or grammatically loose language where precision was required — wrong word choice, awkward sentence structure, register mismatch, or unclear referencing.",
        "never_group": False,
    },

    "argument_structure": {
        "label": "Argument and Structure",
        "description": "Argument or response not coherently developed — point asserted without development, ideas not linked, conclusion not justified, or paragraphing that doesn't carry the reader through the answer.",
        "never_group": False,
    },
}
