# config/mistake_themes/nutrition_and_food_science.py
#
# 4 skills-based mistake categories for Nutrition and Food Science.

THEMES = {

    "content_misconception": {
        "label": "Content Misconception",
        "description": "Student brought the wrong NFS concept to the question — incorrect nutrient function, mistaken food-science principle (e.g. denaturation vs coagulation), or fundamentally incorrect mental model.",
        "never_group": False,
    },

    "application_specificity": {
        "label": "Application to Context",
        "description": "Concept stated in general terms but not applied to the specific context the question gave (e.g. life-stage, dietary need, cooking method, target consumer) — answer reads textbook-generic rather than addressing THIS scenario.",
        "never_group": False,
    },

    "reasoning_gap": {
        "label": "Reasoning Gap",
        "description": "Student identified a nutrient/process/issue but did not link it to the consequence or recommendation the question asked about — missing cause→effect, or stopping short of the practical implication.",
        "never_group": False,
    },

    "terminology_precision": {
        "label": "Terminology and Keywords",
        "description": "Right idea conveyed but the precise NFS term is missing or used loosely — everyday language where syllabus terms (e.g. 'satiety', 'gelatinisation', 'micronutrient') were expected.",
        "never_group": False,
    },
}
