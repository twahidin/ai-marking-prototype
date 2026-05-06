# config/mistake_themes/mathematics.py
#
# 4 skills-based mistake categories for Mathematics (E Math + A Math).
# Skills, NOT topics — apply equally to algebra, geometry, trigonometry,
# calculus, or statistics.

THEMES = {

    "procedural_execution": {
        "label": "Procedural Execution",
        "description": "Right method chosen but executed with an error — arithmetic slip, algebra manipulation mistake, sign error, transcription error, or missed step that a careful re-check would catch.",
        "never_group": False,
    },

    "method_choice": {
        "label": "Method Choice",
        "description": "Wrong method or approach for the problem — applied a formula whose conditions don't fit, chose a technique that doesn't address what the question asked, or picked a roundabout method that fails on this case.",
        "never_group": False,
    },

    "reasoning_gap": {
        "label": "Reasoning and Justification",
        "description": "Answer or working stated without the reasoning that earns the mark — missing justification step in a proof, unstated assumption, or jumping to a result without showing why it follows.",
        "never_group": False,
    },

    "content_misconception": {
        "label": "Content Misconception",
        "description": "Student brought the wrong mathematical concept to the question — misunderstood a definition (e.g. domain, modulus, function), applied a property the wrong way (e.g. log/exponent rules), or held an incorrect mental model.",
        "never_group": False,
    },
}
