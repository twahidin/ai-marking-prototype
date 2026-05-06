# config/mistake_themes/base.py
#
# Universal SKILLS-BASED mistake categories.
#
# Used as the fallback when an assignment's subject doesn't match a
# canonical entry in subjects.py (i.e. the teacher typed a freeform string
# the dropdown couldn't resolve). Subject-specific files in this package
# define their own slim 4-category list and are returned VERBATIM —
# get_themes_for_subject() does NOT merge with this file.
#
# Each subject's dropdown should feel like 4 skills the teacher can match
# at a glance, NOT a syllabus topic list. Keep this file (and every
# subject file) at exactly four entries.
#
# STRUCTURE OF EACH THEME:
#   "key": {
#       "label":       Short display name shown in the dropdown
#       "description": One sentence describing the SKILL that failed —
#                      this is what the AI sees when picking a category
#                      and what the teacher sees as the dropdown subtitle
#       "never_group": If True, never bundles in the student's grouped view
#   }

THEMES = {

    "comprehension": {
        "label": "Question comprehension",
        "description": "Student misread the question or answered something different from what was asked — wrong focus, ignored a qualifier, or addressed only part of what the question demanded.",
        "never_group": False,
    },

    "reasoning_gap": {
        "label": "Reasoning Gap",
        "description": "Student stated a position or partial answer but did not develop the logic — missing steps, missing the link between cause and effect, or missing the key word that earns the mark.",
        "never_group": False,
    },

    "content_misconception": {
        "label": "Content Misconception",
        "description": "Student brought the wrong concept to the question — used an incorrect definition, applied an idea outside its scope, or stated a relationship the wrong way round.",
        "never_group": False,
    },

    "expression_precision": {
        "label": "Expression and Precision",
        "description": "Student conveyed roughly the right idea but the answer was vague, imprecise, or used informal/incorrect terminology where precision was required.",
        "never_group": False,
    },
}
