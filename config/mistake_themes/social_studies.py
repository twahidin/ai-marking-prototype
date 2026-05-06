# config/mistake_themes/social_studies.py
#
# 4 skills-based mistake categories for Social Studies. Skills, NOT
# issue topics — apply equally to SBQs and the structured-response
# (SRQ / EQ) questions across all three issues.

THEMES = {

    "source_evaluation": {
        "label": "Source Evaluation (SBQ)",
        "description": "Source not handled with the expected SBQ skill — purpose/reliability/usefulness not addressed when the question asked for it, source taken at face value, or comparison/cross-reference required by the question not done.",
        "never_group": False,
    },

    "explanation_depth": {
        "label": "Explanation Depth",
        "description": "Factor named but not developed — point asserted without explaining HOW it leads to the outcome, or explanation stops at one link when the question demanded a fuller chain of reasoning.",
        "never_group": False,
    },

    "content_application": {
        "label": "Content Application",
        "description": "Concept or example brought in but not applied to the specific context (e.g. Singapore's situation, the case study scenario) — answer reads generically rather than addressing what THIS question's context demands.",
        "never_group": False,
    },

    "argument_structure": {
        "label": "Argument and Judgement",
        "description": "Argument not structured for the question's demand — no balanced view where 'to what extent' was asked, no clear judgement / stand, or stand asserted without weighing the factors.",
        "never_group": False,
    },
}
