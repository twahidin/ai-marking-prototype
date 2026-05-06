# config/mistake_themes/physics.py
#
# 4 skills-based mistake categories for Physics. Skills, NOT syllabus
# topics — the teacher should be able to match any wrong answer to one
# of these at a glance, regardless of whether the question is about
# kinematics, waves, or radioactivity.

THEMES = {

    "units_quantitative": {
        "label": "Units and Quantitative Care",
        "description": "Lack of attention to units — wrong unit on a quantity, failure to convert between units, or sloppy handling of significant figures and rounding.",
        "never_group": False,
    },

    "equation_application": {
        "label": "Equation Application",
        "description": "Wrong equation chosen for the situation, or the right equation manipulated/applied incorrectly — substituting the wrong variable, rearranging incorrectly, or invoking a formula outside its conditions of use.",
        "never_group": False,
    },

    "reasoning_gap": {
        "label": "Reasoning Gap",
        "description": "Student stated a fact or partial chain but missed the logical steps — missing the effect after stating the cause, missing the connecting principle, or omitting the key term that earns the mark.",
        "never_group": False,
    },

    "content_misconception": {
        "label": "Content Misconception",
        "description": "Student brought the wrong physics concept to the question — invoking an idea that does not apply, stating a relationship the wrong way round, or holding a fundamentally incorrect mental model.",
        "never_group": False,
    },
}
