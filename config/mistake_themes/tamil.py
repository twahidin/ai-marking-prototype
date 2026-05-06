# config/mistake_themes/tamil.py
#
# 4 skills-based mistake categories for Tamil / Higher Tamil.
# Descriptions use both Tamil and English so the AI prompt can ground
# in either language.

THEMES = {

    "vocabulary_precision": {
        "label": "சொற்களஞ்சியம் / Vocabulary",
        "description": "சொல் தேர்வு பிழை — wrong word choice, idiom misused, or everyday phrasing where a more precise term was expected.",
        "never_group": False,
    },

    "grammar_structure": {
        "label": "இலக்கணம் / Grammar and Structure",
        "description": "வாக்கிய அமைப்பு அல்லது இலக்கணப் பிழை — wrong sentence structure, case-marker / verb-form mistakes, or run-on / fragmented sentences.",
        "never_group": False,
    },

    "comprehension": {
        "label": "புரிதல் / Comprehension",
        "description": "கேள்வியை அல்லது பகுதியைத் தவறாகப் புரிந்துகொண்டது — misread the question, missed the focus of the prompt, or addressed only part of what was asked.",
        "never_group": False,
    },

    "expression_coherence": {
        "label": "வெளிப்பாடு / Expression and Coherence",
        "description": "வெளிப்பாட்டில் தொடர்ச்சியின்மை — ideas not linked, weak paragraphing, conclusion not justified, or writing flow that doesn't carry the reader through.",
        "never_group": False,
    },
}
