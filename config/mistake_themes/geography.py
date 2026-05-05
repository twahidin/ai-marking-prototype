# mistake_themes/geography.py
#
# Mistake themes for Geography (O Level / N Level)
# Singapore syllabus — covers physical geography (weather, coasts, tectonics)
# and human geography (food, health, tourism, urbanisation)
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # DATA / MAP / GRAPH QUESTIONS
    # ==================================================================

    "data_not_used": {
        "label": "Data Not Used",
        "description": "Student described a trend without referencing specific figures from the graph, map, or table — answer is too general and does not use the data provided.",
        "never_group": False,
    },

    "data_misread": {
        "label": "Data Misread",
        "description": "Student read an incorrect value from the resource — misread a scale, confused axes, or used the wrong column or row from a table.",
        "never_group": False,
    },

    "map_skill_wrong": {
        "label": "Map Skill Error",
        "description": "Student made an error in a map-based task — e.g. incorrect grid reference format, wrong direction, incorrect contour interpretation.",
        "never_group": False,
    },

    # ==================================================================
    # PHYSICAL GEOGRAPHY
    # ==================================================================

    "process_not_explained": {
        "label": "Geographical Process Not Explained",
        "description": "Student named a geographical process (e.g. erosion, condensation, subduction) but did not explain the mechanism — how or why it occurs.",
        "never_group": False,
    },

    "cause_effect_geography": {
        "label": "Cause and Effect Not Linked",
        "description": "Student identified a geographical cause and a resulting effect but did not explicitly connect the two — the chain of reasoning was incomplete.",
        "never_group": False,
    },

    "landform_formation_incomplete": {
        "label": "Landform Formation Incomplete",
        "description": "Student described the appearance of a landform but did not fully explain all the stages of its formation — steps in the sequence were missing or out of order.",
        "never_group": False,
    },

    "weather_climate_confused": {
        "label": "Weather and Climate Confused",
        "description": "Student confused weather (short-term atmospheric conditions) with climate (long-term average) — used the terms interchangeably or described one when asked about the other.",
        "never_group": False,
    },

    # ==================================================================
    # HUMAN GEOGRAPHY
    # ==================================================================

    "case_study_not_used": {
        "label": "Case Study Not Referenced",
        "description": "Student gave a generic answer without referencing a specific place, country, or case study — question required a named example with supporting detail.",
        "never_group": False,
    },

    "case_study_detail_lacking": {
        "label": "Case Study Too Vague",
        "description": "Student named a case study but did not provide specific facts, figures, or details — reference was too general to earn more than one mark.",
        "never_group": False,
    },

    "strategy_not_evaluated": {
        "label": "Strategy Not Evaluated",
        "description": "Student described a management strategy but did not evaluate its effectiveness — did not assess whether it worked, its limitations, or its impact.",
        "never_group": False,
    },

    "development_concept_wrong": {
        "label": "Development Concept Wrong",
        "description": "Student used a development concept incorrectly — e.g. confused HDI with GDP, or described a LEDC strategy without acknowledging the development context.",
        "never_group": False,
    },

    # ==================================================================
    # EXAM TECHNIQUE
    # ==================================================================

    "describe_not_explain": {
        "label": "Described Instead of Explained",
        "description": "Student described a pattern or feature when the question asked for an explanation — stated what without addressing why.",
        "never_group": False,
    },

    "one_sided_answer": {
        "label": "One-Sided Answer",
        "description": "Question required discussion of advantages and disadvantages, or multiple perspectives, but student addressed only one side.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
