# mistake_themes/chemistry.py
#
# Mistake themes for Chemistry (O Level / N Level)
# Singapore syllabus — covers atomic structure, bonding, stoichiometry,
# acids/bases, redox, electrolysis, organic chemistry, energy changes
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # EQUATIONS & FORMULAE
    # ==================================================================

    "equation_unbalanced": {
        "label": "Equation Not Balanced",
        "description": "Student wrote a chemical equation that is not balanced for atoms or charge — number of atoms on left and right do not match.",
        "never_group": False,
    },

    "state_symbol_wrong": {
        "label": "State Symbol Wrong or Missing",
        "description": "Student omitted or incorrectly assigned state symbols (s), (l), (aq), (g) in a chemical equation.",
        "never_group": False,
    },

    "formula_wrong": {
        "label": "Chemical Formula Wrong",
        "description": "Student used an incorrect chemical formula — e.g. wrote CO for CO₂, or wrong ionic formula due to incorrect valency used.",
        "never_group": False,
    },

    "ionic_equation_not_written": {
        "label": "Ionic Equation Not Written",
        "description": "Question required a full ionic or net ionic equation but student wrote only the molecular equation.",
        "never_group": False,
    },

    # ==================================================================
    # ATOMIC STRUCTURE & BONDING
    # ==================================================================

    "proton_neutron_confused": {
        "label": "Proton and Neutron Confused",
        "description": "Student confused the number of protons and neutrons — e.g. used mass number as proton number, or described neutrons as determining the element.",
        "never_group": False,
    },

    "ionic_covalent_confused": {
        "label": "Ionic and Covalent Bonding Confused",
        "description": "Student described the wrong type of bonding — e.g. described electron sharing for an ionic compound, or electron transfer for a covalent one.",
        "never_group": False,
    },

    "dot_cross_diagram_wrong": {
        "label": "Dot-and-Cross Diagram Wrong",
        "description": "Student drew an incorrect dot-and-cross diagram — wrong number of electrons, wrong shell arrangement, or electrons not shown in pairs.",
        "never_group": False,
    },

    "metallic_bonding_incomplete": {
        "label": "Metallic Bonding Incomplete",
        "description": "Student described metallic bonding without mentioning the sea of delocalised electrons or the electrostatic attraction between electrons and positive ions.",
        "never_group": False,
    },

    # ==================================================================
    # STOICHIOMETRY & MOLES
    # ==================================================================

    "mole_calculation_wrong": {
        "label": "Mole Calculation Error",
        "description": "Student made an error in a mole calculation — wrong formula used (n = m/Mr confused with n = V/24), or wrong molar mass used.",
        "never_group": False,
    },

    "molar_ratio_ignored": {
        "label": "Molar Ratio Ignored",
        "description": "Student used moles directly without applying the stoichiometric ratio from the balanced equation — gave a 1:1 ratio when the equation shows otherwise.",
        "never_group": False,
    },

    "limiting_reagent_missed": {
        "label": "Limiting Reagent Not Identified",
        "description": "Student did not identify the limiting reagent and calculated yield based on excess reagent — giving an answer larger than the actual yield.",
        "never_group": False,
    },

    # ==================================================================
    # ACIDS, BASES & SALTS
    # ==================================================================

    "acid_base_definition_wrong": {
        "label": "Acid/Base Definition Wrong",
        "description": "Student defined an acid or base incorrectly — e.g. defined acid by taste or colour rather than H⁺ ion donation, or confused Arrhenius with Bronsted-Lowry definitions.",
        "never_group": False,
    },

    "salt_preparation_wrong": {
        "label": "Wrong Salt Preparation Method",
        "description": "Student chose an inappropriate method to prepare a salt — e.g. suggested titration for an insoluble salt, or precipitation for a soluble salt.",
        "never_group": False,
    },

    "neutralisation_incomplete": {
        "label": "Neutralisation Equation Incomplete",
        "description": "Student wrote a neutralisation reaction but omitted water as a product, or gave wrong products for the specific acid-base pair.",
        "never_group": False,
    },

    # ==================================================================
    # REDOX & ELECTROCHEMISTRY
    # ==================================================================

    "oxidation_reduction_confused": {
        "label": "Oxidation and Reduction Confused",
        "description": "Student confused oxidation and reduction — e.g. stated oxidation involves gain of electrons, or confused which species is oxidised vs reduced.",
        "never_group": False,
    },

    "oxidation_state_wrong": {
        "label": "Oxidation State Wrong",
        "description": "Student calculated an incorrect oxidation state — common errors involve ignoring the overall charge of an ion or applying the rules in the wrong order.",
        "never_group": False,
    },

    "electrolysis_electrode_wrong": {
        "label": "Electrolysis Electrode Product Wrong",
        "description": "Student predicted the wrong product at the anode or cathode — common errors involve selective discharge rules when multiple ions are present.",
        "never_group": False,
    },

    "electrolysis_active_inert_confused": {
        "label": "Active and Inert Electrode Confused",
        "description": "Student did not account for whether the electrode is active (dissolves) or inert (does not react) when predicting electrolysis products.",
        "never_group": False,
    },

    # ==================================================================
    # ORGANIC CHEMISTRY
    # ==================================================================

    "homologous_series_confused": {
        "label": "Homologous Series Confused",
        "description": "Student confused alkanes, alkenes, and alcohols — wrong general formula, wrong functional group, or wrong reaction type stated.",
        "never_group": False,
    },

    "addition_substitution_confused": {
        "label": "Addition and Substitution Confused",
        "description": "Student confused addition reactions (alkenes) with substitution reactions (alkanes) — applied the wrong reaction type for the given compound.",
        "never_group": False,
    },

    "structural_formula_wrong": {
        "label": "Structural Formula Wrong",
        "description": "Student drew an incorrect structural formula — wrong number of bonds, missing functional group, or incorrect carbon chain length.",
        "never_group": False,
    },

    # ==================================================================
    # ENERGY CHANGES
    # ==================================================================

    "exothermic_endothermic_confused": {
        "label": "Exothermic and Endothermic Confused",
        "description": "Student confused exothermic and endothermic reactions — wrong sign for energy change, or wrong direction of heat flow described.",
        "never_group": False,
    },

    "bond_energy_direction_wrong": {
        "label": "Bond Energy Direction Wrong",
        "description": "Student confused energy released (bond forming) with energy absorbed (bond breaking) when calculating energy changes from bond energies.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
