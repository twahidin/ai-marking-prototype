# config/mistake_themes/literature_in_english.py
#
# 4 skills-based mistake categories for Literature in English. Skills,
# NOT specific texts or genres — apply equally to poetry, prose, and
# drama responses.

THEMES = {

    "text_comprehension": {
        "label": "Text Comprehension",
        "description": "Student misread the extract or the question's focus — confused speaker with poet, missed a key shift in the text, or addressed the text's surface meaning when the question called for a deeper reading.",
        "never_group": False,
    },

    "evidence_use": {
        "label": "Evidence and Quotation",
        "description": "Evidence missing, mis-quoted, or not unpacked — quote omitted where required, embedded loosely without comment, or selected evidence does not support the point being made.",
        "never_group": False,
    },

    "analysis_depth": {
        "label": "Depth of Analysis",
        "description": "Technique named but not analysed — feature spotted (metaphor, alliteration, irony) without explaining its effect, or analysis stops at retelling the text instead of examining how meaning is created.",
        "never_group": False,
    },

    "expression_precision": {
        "label": "Language and Expression",
        "description": "Imprecise or loose critical writing — wrong word choice, awkward phrasing, ideas not coherently linked, or argument restated rather than developed.",
        "never_group": False,
    },
}
