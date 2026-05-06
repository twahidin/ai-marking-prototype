# config/mistake_themes/chinese.py
#
# 4 skills-based mistake categories for Chinese / Higher Chinese.
# Descriptions use both Chinese and English so the AI prompt — which may
# run in either language depending on the assignment — can ground in
# either.

THEMES = {

    "vocabulary_precision": {
        "label": "词语运用 / Vocabulary",
        "description": "用词不准确或不恰当 — wrong word choice, idiom (成语) misused or misspelled, or everyday phrasing where a more precise term was expected.",
        "never_group": False,
    },

    "grammar_structure": {
        "label": "语法句式 / Grammar and Structure",
        "description": "句子结构、语序或语法错误 — wrong sentence structure, mis-ordered components, particle (了/着/过) misuse, or run-on/fragmented sentences.",
        "never_group": False,
    },

    "comprehension": {
        "label": "理解 / Comprehension",
        "description": "对题目或文段理解有偏差 — misread the question, missed the focus of the prompt, or addressed only part of what was asked.",
        "never_group": False,
    },

    "expression_coherence": {
        "label": "表达连贯 / Expression and Coherence",
        "description": "表达不连贯或逻辑松散 — ideas not linked, paragraphing weak, conclusion not justified, or writing flow that doesn't carry the reader through.",
        "never_group": False,
    },
}
