# config/mistake_themes/history.py
#
# 4 skills-based mistake categories for History. Skills, NOT specific
# topics — apply equally to source-based questions, structured-essay
# questions, and any historical period.

THEMES = {

    "source_handling": {
        "label": "Source Handling",
        "description": "Source not used effectively — provenance ignored, content described instead of inferred from, source taken at face value without considering reliability/purpose, or sources not cross-referenced when the question asked for it.",
        "never_group": False,
    },

    "explanation_depth": {
        "label": "Explanation Depth",
        "description": "Factor identified but not developed — point asserted without explaining HOW it caused the outcome, or explanation stops at one link when the question demanded a chain.",
        "never_group": False,
    },

    "content_accuracy": {
        "label": "Content Accuracy",
        "description": "Factual content wrong, vague, or missing — wrong date / event / figure / treaty, or generalisation given where a specific historical detail was needed.",
        "never_group": False,
    },

    "argument_structure": {
        "label": "Argument and Judgement",
        "description": "Argument not structured for the question's demand — no balanced consideration where 'how far' was asked, no judgement given, or judgement asserted without weighing the factors.",
        "never_group": False,
    },
}
