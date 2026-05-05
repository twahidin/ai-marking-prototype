# mistake_themes/english.py
#
# Mistake themes for English Language (O Level / N Level / N Technical)
# Singapore syllabus — covers Paper 1 (Writing) and Paper 2 (Comprehension, Summary, Vocabulary)
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES
# ──────────────────────────────────────────────────────────────────────
# Copy the block below and fill in your own key, label, and description.
# Add it anywhere inside the THEMES dict.
#
#   "your_key_here": {
#       "label":       "Short Name (3-5 words)",
#       "description": "One sentence: what exactly went wrong. Be specific enough
#                       that two different error types would get two different labels.",
#       "never_group": False,   # set True only if this is a one-off error type
#   },                          # that never forms a pattern across questions
#
# GOOD label + description:
#   "label":       "Inference Not Explained"
#   "description": "Student identified the correct inference but did not explain
#                   the reasoning that led to it — just stated the conclusion."
#
# BAD label + description (too generic):
#   "label":       "Comprehension Error"
#   "description": "Student did not understand the passage."
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # PAPER 2 — COMPREHENSION
    # ==================================================================

    "surface_lifting": {
        "label": "Lifted Without Own Words",
        "description": "Student copied phrases or sentences directly from the passage instead of paraphrasing — answer reads as a quotation, not an explanation.",
        "never_group": False,
    },

    "inference_not_made": {
        "label": "Inference Not Made",
        "description": "Student answered at a literal/surface level when the question required reading between the lines — restated what the passage said rather than what it implied.",
        "never_group": False,
    },

    "inference_unsupported": {
        "label": "Inference Not Supported",
        "description": "Student made an inference but did not link it back to specific words or evidence from the passage to justify the conclusion.",
        "never_group": False,
    },

    "wrong_passage_section": {
        "label": "Wrong Part of Passage Used",
        "description": "Student drew on the wrong paragraph or section — answer is plausible but not based on the lines the question refers to.",
        "never_group": False,
    },

    "tone_purpose_missed": {
        "label": "Writer's Tone or Purpose Missed",
        "description": "Student described what the writer said but not how or why — missed the tone, attitude, or purpose behind the writer's word choice.",
        "never_group": False,
    },

    "language_effect_missed": {
        "label": "Language Effect Not Explained",
        "description": "Student identified a language feature (e.g. metaphor, alliteration) but did not explain the effect it creates on the reader.",
        "never_group": False,
    },

    "vocabulary_context_ignored": {
        "label": "Vocabulary Meaning Out of Context",
        "description": "Student gave a dictionary definition of a word without explaining what it means in the specific context of the passage.",
        "never_group": False,
    },

    # ==================================================================
    # PAPER 2 — SUMMARY
    # ==================================================================

    "summary_wrong_points": {
        "label": "Wrong Points Selected",
        "description": "Student identified points that do not answer the summary question — selected general or irrelevant content instead of the specific points asked for.",
        "never_group": False,
    },

    "summary_too_vague": {
        "label": "Summary Point Too Vague",
        "description": "Student identified the correct area of the passage but expressed the point too generally — key detail or qualifier that earns the mark was omitted.",
        "never_group": False,
    },

    "summary_lifting": {
        "label": "Summary Lifted From Passage",
        "description": "Student copied the passage's phrasing too closely instead of paraphrasing — penalised for not using own words in summary.",
        "never_group": False,
    },

    "summary_over_count": {
        "label": "Exceeded Word Limit",
        "description": "Student's summary exceeded the word limit, risking penalty under exam conditions.",
        "never_group": True,
    },

    # ==================================================================
    # PAPER 1 — SITUATIONAL WRITING
    # ==================================================================

    "situational_format_wrong": {
        "label": "Wrong Format Used",
        "description": "Student used the wrong layout for the task — e.g. wrote a letter when an email was required, or omitted required sections like subject line, salutation, or sign-off.",
        "never_group": False,
    },

    "situational_register_wrong": {
        "label": "Wrong Register",
        "description": "Student's tone or language level did not match the audience or purpose — too informal for a formal piece, or too stiff for an informal one.",
        "never_group": False,
    },

    "situational_content_vague": {
        "label": "Content Too Vague",
        "description": "Student addressed the task but gave no specific details, examples, or reasons — points are generic and could apply to any situation.",
        "never_group": False,
    },

    "situational_purpose_missed": {
        "label": "Purpose of Task Missed",
        "description": "Student misread the task purpose — e.g. wrote to inform when the task was to persuade, or wrote to the wrong audience.",
        "never_group": False,
    },

    "situational_points_insufficient": {
        "label": "Too Few Points Made",
        "description": "Student developed only one or two points when the task and mark allocation required more — answer is too thin for the marks available.",
        "never_group": False,
    },

    # ==================================================================
    # PAPER 1 — CONTINUOUS WRITING
    # ==================================================================

    "continuous_no_structure": {
        "label": "No Clear Structure",
        "description": "Essay lacks a clear introduction, body, or conclusion — ideas are presented as a single block without paragraphing or logical progression.",
        "never_group": False,
    },

    "continuous_weak_topic_sentence": {
        "label": "Weak Topic Sentence",
        "description": "Paragraph does not open with a clear main idea — reader cannot tell what the paragraph is about from the first sentence.",
        "never_group": False,
    },

    "continuous_undeveloped_point": {
        "label": "Point Not Developed",
        "description": "Student stated an idea but did not elaborate — no explanation, example, or analysis to support the point beyond the opening sentence.",
        "never_group": False,
    },

    "continuous_narrative_no_reflection": {
        "label": "Narration Without Reflection",
        "description": "Student recounted events in sequence but did not reflect on feelings, significance, or meaning — reads as a plot summary rather than personal writing.",
        "never_group": False,
    },

    "continuous_off_topic": {
        "label": "Off Topic",
        "description": "Essay drifts from the question — student wrote about a related but different topic, or ignored the specific angle or keyword in the title.",
        "never_group": False,
    },

    # ==================================================================
    # LANGUAGE ACROSS BOTH PAPERS
    # ==================================================================

    "tense_inconsistency": {
        "label": "Tense Inconsistency",
        "description": "Student switched between tenses within a piece without reason — past and present tense mixed in the same paragraph or sentence.",
        "never_group": False,
    },

    "mother_tongue_interference": {
        "label": "Mother Tongue Interference",
        "description": "Sentence structure is grammatically incorrect in a way typical of direct translation from another language — e.g. missing articles, inverted word order.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
