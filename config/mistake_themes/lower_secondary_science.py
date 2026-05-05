# mistake_themes/lower_secondary_science.py
#
# Mistake themes for Lower Secondary Science (Sec 1 & 2)
# Singapore syllabus — integrated Science covering Physics, Chemistry,
# and Biology fundamentals before subject specialisation
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    "lss_everyday_science_confused": {
        "label": "Everyday Concept Confused With Science",
        "description": "Student applied a common everyday understanding that contradicts the scientific definition — e.g. 'cold enters the room' instead of 'heat leaves the room'.",
        "never_group": False,
    },

    "lss_process_not_explained": {
        "label": "Process Not Explained",
        "description": "Student named a scientific process but did not explain how or why it occurs — stated the outcome without the mechanism.",
        "never_group": False,
    },

    "lss_unit_omitted": {
        "label": "Unit Omitted",
        "description": "Student gave a correct numerical answer but did not include the unit — e.g. wrote 9.8 instead of 9.8 N/kg.",
        "never_group": False,
    },

    "lss_observation_vs_inference": {
        "label": "Observation and Inference Confused",
        "description": "Student stated an inference as if it were an observation, or described an observation when an inference was asked for.",
        "never_group": False,
    },

    "lss_variable_not_controlled": {
        "label": "Control Variable Not Identified",
        "description": "In an experiment question, student did not identify variables that must be kept constant, or confused the independent, dependent, and control variables.",
        "never_group": False,
    },

    "lss_fair_test_incomplete": {
        "label": "Fair Test Not Explained",
        "description": "Student described an experiment but did not explain how it was made a fair test — did not state what was kept constant and why.",
        "never_group": False,
    },

    "lss_keyword_missing": {
        "label": "Science Keyword Missing",
        "description": "Student explained the concept in everyday language but omitted the required scientific term that earns the mark.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # ==================================================================
}
