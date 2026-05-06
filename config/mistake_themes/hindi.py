# config/mistake_themes/hindi.py
#
# 4 skills-based mistake categories for Hindi.
# Descriptions use both Hindi and English so the AI prompt can ground
# in either language.

THEMES = {

    "vocabulary_precision": {
        "label": "शब्दावली / Vocabulary",
        "description": "शब्द-चयन में त्रुटि — wrong word choice, muhāvarā (idiom) misused, or everyday phrasing where a more precise term was expected.",
        "never_group": False,
    },

    "grammar_structure": {
        "label": "व्याकरण / Grammar and Structure",
        "description": "वाक्य-संरचना या व्याकरण की त्रुटि — wrong sentence structure, mismatched gender / number / verb forms, or run-on / fragmented sentences.",
        "never_group": False,
    },

    "comprehension": {
        "label": "समझ / Comprehension",
        "description": "प्रश्न या गद्यांश की गलत समझ — misread the question, missed the focus of the prompt, or addressed only part of what was asked.",
        "never_group": False,
    },

    "expression_coherence": {
        "label": "अभिव्यक्ति / Expression and Coherence",
        "description": "अभिव्यक्ति में निरंतरता का अभाव — ideas not linked, weak paragraphing, conclusion not justified, or writing flow that doesn't carry the reader through.",
        "never_group": False,
    },
}
