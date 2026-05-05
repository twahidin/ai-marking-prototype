# mistake_themes/history.py
#
# Mistake themes for History (O Level / N Level)
# Singapore syllabus — covers source-based case study (SBQ) and
# structured essay questions (SEQ) on 20th century history
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # SOURCE-BASED QUESTIONS (SBQ)
    # ==================================================================

    "sbq_no_inference": {
        "label": "No Inference From Source",
        "description": "Student described what the source shows at face value but did not make an inference — did not explain what the source suggests or implies beyond the literal content.",
        "never_group": False,
    },

    "sbq_inference_unsupported": {
        "label": "Inference Not Supported by Source",
        "description": "Student made an inference but did not quote or reference specific evidence from the source to support it.",
        "never_group": False,
    },

    "sbq_purpose_missed": {
        "label": "Source Purpose Not Addressed",
        "description": "Student did not address why the source was produced — missed the author's purpose, intended audience, or the context in which it was created.",
        "never_group": False,
    },

    "sbq_provenance_ignored": {
        "label": "Provenance Not Used",
        "description": "Student evaluated reliability or utility without considering the provenance — who wrote it, when, and why — which is essential for a full reliability answer.",
        "never_group": False,
    },

    "sbq_comparison_one_sided": {
        "label": "Comparison One-Sided",
        "description": "Student compared sources but addressed only one source in detail — did not make an explicit cross-reference between both sources.",
        "never_group": False,
    },

    "sbq_utility_confused_with_reliability": {
        "label": "Utility and Reliability Confused",
        "description": "Student answered a utility question as if it were a reliability question, or vice versa — did not address what the source is useful for and to whom.",
        "never_group": False,
    },

    # ==================================================================
    # STRUCTURED ESSAY QUESTIONS (SEQ)
    # ==================================================================

    "seq_no_argument": {
        "label": "No Argument Made",
        "description": "Student wrote factually accurate content but did not make a clear historical argument — listed facts or events without explaining their significance or answering the question.",
        "never_group": False,
    },

    "seq_evidence_not_linked": {
        "label": "Evidence Not Linked to Argument",
        "description": "Student provided historical evidence but did not explain how it supports the argument being made — evidence and argument are presented separately.",
        "never_group": False,
    },

    "seq_one_factor_only": {
        "label": "Only One Factor Discussed",
        "description": "Question required consideration of multiple factors but student developed only one — did not address the required breadth of causes, effects, or perspectives.",
        "never_group": False,
    },

    "seq_judgement_missing": {
        "label": "Judgement Not Made",
        "description": "Student discussed factors but did not make a final judgement or reach a conclusion — did not answer 'how far', 'to what extent', or 'which was more important'.",
        "never_group": False,
    },

    "seq_wrong_time_period": {
        "label": "Wrong Time Period Referenced",
        "description": "Student used historical examples from outside the time period specified in the question, or confused the chronological sequence of events.",
        "never_group": False,
    },

    "seq_narrative_not_analytical": {
        "label": "Narrative Not Analytical",
        "description": "Student told the story of what happened without analysing why it happened or what it meant — response reads as a timeline rather than a historical argument.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
