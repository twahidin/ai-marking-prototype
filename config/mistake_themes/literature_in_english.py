# mistake_themes/literature_in_english.py
#
# Mistake themes for Literature in English (O Level / N Level)
# Singapore syllabus — covers prose, poetry, drama; unseen and set texts;
# essay questions and passage-based questions
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # CLOSE READING & LANGUAGE ANALYSIS
    # ==================================================================

    "lit_technique_named_not_analysed": {
        "label": "Technique Named, Not Analysed",
        "description": "Student identified a literary technique (e.g. metaphor, alliteration, irony) but did not analyse its effect on the reader or its contribution to meaning.",
        "never_group": False,
    },

    "lit_no_quotation": {
        "label": "No Quotation Used",
        "description": "Student made a point about the text but did not support it with a direct quotation — assertion without evidence.",
        "never_group": False,
    },

    "lit_quotation_not_unpacked": {
        "label": "Quotation Not Unpacked",
        "description": "Student embedded a quotation correctly but did not explain what specific words or phrases in it contribute to the point being made.",
        "never_group": False,
    },

    "lit_paraphrase_not_analysis": {
        "label": "Paraphrase Instead of Analysis",
        "description": "Student restated what happens in the text in different words instead of analysing language, structure, or technique — narrated rather than analysed.",
        "never_group": False,
    },

    "lit_effect_on_reader_missed": {
        "label": "Effect on Reader Missed",
        "description": "Student analysed the text but did not connect the analysis to the effect created in the reader — the 'so what' of the language choice was absent.",
        "never_group": False,
    },

    # ==================================================================
    # ESSAY STRUCTURE & ARGUMENT
    # ==================================================================

    "lit_no_argument": {
        "label": "No Literary Argument",
        "description": "Student wrote about the text but did not construct a clear argument — response was a series of observations rather than a sustained interpretive case.",
        "never_group": False,
    },

    "lit_argument_not_sustained": {
        "label": "Argument Not Sustained",
        "description": "Student started with a clear argument but lost it mid-essay — later paragraphs drifted into different or contradictory points without tying back to the central claim.",
        "never_group": False,
    },

    "lit_context_not_used": {
        "label": "Context Not Integrated",
        "description": "Student had relevant contextual knowledge (historical, biographical, social) but either did not use it or mentioned it without linking it to the text's meaning.",
        "never_group": False,
    },

    "lit_question_focus_lost": {
        "label": "Question Focus Lost",
        "description": "Student wrote accurately about the text but drifted from the specific focus of the question — general literary discussion rather than targeted response.",
        "never_group": False,
    },

    # ==================================================================
    # POETRY SPECIFIC
    # ==================================================================

    "poetry_form_ignored": {
        "label": "Poem Form Ignored",
        "description": "Student analysed the poem's language but ignored structural or formal features — rhyme scheme, line breaks, stanza structure, or rhythm — that contribute to meaning.",
        "never_group": False,
    },

    "poetry_speaker_confused_with_poet": {
        "label": "Speaker Confused With Poet",
        "description": "Student referred to the speaker of the poem as 'the poet' or assumed the speaker's views are the poet's own views without qualification.",
        "never_group": False,
    },

    # ==================================================================
    # DRAMA SPECIFIC
    # ==================================================================

    "drama_stagecraft_ignored": {
        "label": "Stagecraft Ignored",
        "description": "Student analysed dialogue and character but ignored stage directions, setting, or performance elements that shape meaning in the drama.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
