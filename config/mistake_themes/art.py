# config/mistake_themes/art.py
#
# 4 skills-based mistake categories for Art.

THEMES = {

    "analysis_depth": {
        "label": "Depth of Visual Analysis",
        "description": "Visual element named but not analysed — formal feature identified (line, colour, texture, composition) without explaining its effect or how it shapes meaning, or analysis stops at description.",
        "never_group": False,
    },

    "contextual_understanding": {
        "label": "Contextual Understanding",
        "description": "Work / artist / movement discussed without grounding in its historical, cultural, or art-historical context — context missing, generic, or inaccurate for the specific work being discussed.",
        "never_group": False,
    },

    "terminology_precision": {
        "label": "Art Terminology",
        "description": "Right idea conveyed but the precise art term is missing or used loosely — everyday language where syllabus vocabulary (e.g. 'chiaroscuro', 'impasto', 'composition') was expected.",
        "never_group": False,
    },

    "expression_precision": {
        "label": "Written Expression",
        "description": "Imprecise or loose written response — wrong word choice, awkward phrasing, ideas not coherently linked, or argument restated rather than developed.",
        "never_group": False,
    },
}
