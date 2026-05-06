# config/mistake_themes/chemistry.py
#
# 4 skills-based mistake categories for Chemistry. Skills, NOT syllabus
# topics — apply equally to mole calculations, acids/bases, organic, or
# qualitative analysis.

THEMES = {

    "units_quantitative": {
        "label": "Units and Quantitative Care",
        "description": "Lack of attention to units or quantities — wrong unit on a quantity (mol, mol/dm³, g, dm³), conversion errors, mole-ratio mishandling, or sloppy significant figures.",
        "never_group": False,
    },

    "equation_application": {
        "label": "Equation and Formula Application",
        "description": "Wrong chemical equation, formula, or balancing — incorrect product, wrong state symbols, unbalanced charges/atoms, or applying stoichiometric ratios incorrectly.",
        "never_group": False,
    },

    "reasoning_gap": {
        "label": "Reasoning Gap",
        "description": "Student stated an observation or partial chain but missed the logical step — observation without inference, mechanism without the explaining principle, or missing the key term (e.g. 'donates a proton', 'oxidising agent') that earns the mark.",
        "never_group": False,
    },

    "content_misconception": {
        "label": "Content Misconception",
        "description": "Student brought the wrong chemistry concept to the question — invoking a trend or rule outside its conditions, mixing up oxidation and reduction, or holding an incorrect mental model of bonding/reactivity.",
        "never_group": False,
    },
}
