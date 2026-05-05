# mistake_themes/social_studies.py
#
# Mistake themes for Social Studies (O Level / N Level)
# Singapore syllabus — covers SBQ (source-based questions) and
# extended writing on governance, identity, and globalisation
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # SOURCE-BASED QUESTIONS (SBQ)
    # ==================================================================

    "ss_no_inference": {
        "label": "No Inference Made",
        "description": "Student described the source content literally but did not make an inference about what it suggests, implies, or reveals about the issue.",
        "never_group": False,
    },

    "ss_inference_unsupported": {
        "label": "Inference Not Supported",
        "description": "Student made an inference but did not quote or reference specific evidence from the source to justify it.",
        "never_group": False,
    },

    "ss_purpose_missed": {
        "label": "Source Purpose Missed",
        "description": "Student did not consider why the source was produced — missed the author's viewpoint, intended audience, or the context shaping the message.",
        "never_group": False,
    },

    "ss_surprise_not_explained": {
        "label": "'Surprised By' Not Explained",
        "description": "Student stated whether they are surprised by a source but did not explain what prior knowledge or expectation made the source surprising or unsurprising.",
        "never_group": False,
    },

    "ss_comparison_incomplete": {
        "label": "Source Comparison Incomplete",
        "description": "Student compared sources but only addressed one source in depth — did not explicitly cross-reference between the two sources on the same point.",
        "never_group": False,
    },

    "ss_hybrid_question_missed": {
        "label": "Hybrid Question Format Missed",
        "description": "Question required both source evidence and own knowledge but student used only one — did not integrate the source with contextual knowledge, or vice versa.",
        "never_group": False,
    },

    # ==================================================================
    # EXTENDED WRITING
    # ==================================================================

    "ss_no_stand": {
        "label": "No Stand Taken",
        "description": "Student did not take a clear position or stand — answered 'discuss' questions by listing points on both sides without committing to a view.",
        "never_group": False,
    },

    "ss_evidence_generic": {
        "label": "Evidence Too Generic",
        "description": "Student supported a point with vague or general statements — did not use specific Singapore examples, statistics, or policy details.",
        "never_group": False,
    },

    "ss_issue_not_linked_to_singapore": {
        "label": "Not Linked to Singapore Context",
        "description": "Student discussed the issue in abstract or global terms without linking it to the Singapore context required by the syllabus.",
        "never_group": False,
    },

    "ss_one_perspective_only": {
        "label": "Only One Perspective Addressed",
        "description": "Question required consideration of multiple stakeholder perspectives but student focused entirely on one group's viewpoint.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
