# config/mistake_themes/malay.py
#
# 4 skills-based mistake categories for Malay / Higher Malay.
# Descriptions use both Bahasa Melayu and English so the AI prompt
# can ground in either language.

THEMES = {

    "vocabulary_precision": {
        "label": "Kosa Kata / Vocabulary",
        "description": "Pemilihan perkataan tidak tepat — wrong word choice, idiom (peribahasa / simpulan bahasa) misused, or everyday phrasing where a more precise term was expected.",
        "never_group": False,
    },

    "grammar_structure": {
        "label": "Tatabahasa / Grammar and Structure",
        "description": "Struktur ayat atau tatabahasa salah — wrong sentence structure, imbuhan (prefix/suffix) misuse, or run-on / fragmented sentences.",
        "never_group": False,
    },

    "comprehension": {
        "label": "Pemahaman / Comprehension",
        "description": "Salah faham soalan atau teks — misread the question, missed the focus of the prompt, or addressed only part of what was asked.",
        "never_group": False,
    },

    "expression_coherence": {
        "label": "Pengungkapan / Expression and Coherence",
        "description": "Pengungkapan tidak koheren — ideas not linked, weak paragraphing, conclusion not justified, or writing flow that doesn't carry the reader through.",
        "never_group": False,
    },
}
