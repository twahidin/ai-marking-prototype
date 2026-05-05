# mistake_themes/nutrition_and_food_science.py
#
# Mistake themes for Nutrition and Food Science (O Level / N Level)
# Singapore syllabus — covers nutrients, meal planning, food preparation,
# food safety, and consumer education
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    "nfs_nutrient_function_wrong": {
        "label": "Nutrient Function Wrong",
        "description": "Student attributed the wrong function to a nutrient — e.g. stated that carbohydrates build and repair tissue, or that protein is the primary energy source.",
        "never_group": False,
    },

    "nfs_deficiency_wrong": {
        "label": "Wrong Deficiency Disease Named",
        "description": "Student linked a nutrient to the wrong deficiency disease — e.g. stated that lack of Vitamin C causes rickets instead of scurvy.",
        "never_group": False,
    },

    "nfs_food_group_wrong": {
        "label": "Food Group Classification Wrong",
        "description": "Student placed a food item in the wrong food group — e.g. classified cheese as a fat rather than a dairy/protein source.",
        "never_group": False,
    },

    "nfs_hygiene_reason_missing": {
        "label": "Food Safety Reason Missing",
        "description": "Student stated a food hygiene or safety practice but did not explain why it is necessary — the reason linked to bacterial growth or contamination was absent.",
        "never_group": False,
    },

    "nfs_cooking_method_wrong": {
        "label": "Wrong Cooking Method for Nutrient",
        "description": "Student recommended a cooking method that would destroy the nutrient in question — e.g. boiling for water-soluble vitamins without noting nutrient loss.",
        "never_group": False,
    },

    "nfs_meal_plan_not_balanced": {
        "label": "Meal Plan Not Balanced",
        "description": "Student's suggested meal plan did not meet the dietary requirements — missing food groups, wrong proportions, or did not address the specific dietary need in the question.",
        "never_group": False,
    },

    "nfs_target_group_ignored": {
        "label": "Target Group Needs Ignored",
        "description": "Question specified a target group (e.g. elderly, pregnant women, athletes) but student gave a generic answer without addressing the specific nutritional needs of that group.",
        "never_group": False,
    },

    "nfs_sensory_term_wrong": {
        "label": "Sensory Evaluation Term Wrong",
        "description": "Student used sensory evaluation terms incorrectly — e.g. confused 'texture' with 'appearance', or described taste when aroma was asked.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # ==================================================================
}
