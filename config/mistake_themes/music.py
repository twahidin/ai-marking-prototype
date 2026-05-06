# config/mistake_themes/music.py
#
# 4 skills-based mistake categories for Music.

THEMES = {

    "theory_application": {
        "label": "Theory Application",
        "description": "Music-theory concept misapplied — wrong key/scale identified, harmony/cadence labelled incorrectly, rhythmic notation written wrongly, or theory rule applied outside its conditions.",
        "never_group": False,
    },

    "analysis_depth": {
        "label": "Depth of Analysis",
        "description": "Musical feature named but not analysed — element identified (texture, dynamics, instrumentation) without explaining its effect or function, or analysis stops at description rather than examining how meaning/style is created.",
        "never_group": False,
    },

    "terminology_precision": {
        "label": "Terminology and Keywords",
        "description": "Right idea conveyed but the precise musical term is missing or used loosely — everyday language used where the syllabus term was expected (e.g. 'gets louder' instead of 'crescendo').",
        "never_group": False,
    },

    "expression_precision": {
        "label": "Written Expression",
        "description": "Imprecise or loose written response — wrong word choice, awkward phrasing, ideas not coherently linked, or argument restated rather than developed.",
        "never_group": False,
    },
}
