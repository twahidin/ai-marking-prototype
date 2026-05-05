# mistake_themes/physics.py
#
# Mistake themes for Physics (O Level / N Level)
# Singapore syllabus — covers measurement, kinematics, dynamics, pressure,
# energy, waves, light, electromagnetism, radioactivity
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # MEASUREMENT & UNITS
    # ==================================================================

    "unit_wrong": {
        "label": "Wrong Unit",
        "description": "Student gave the correct numerical answer but with an incorrect unit — e.g. gave speed in m instead of m/s, or energy in W instead of J.",
        "never_group": False,
    },

    "unit_not_converted": {
        "label": "Unit Not Converted",
        "description": "Student used values in inconsistent units without converting — e.g. mixed km/h with m/s, or grams with kilograms in the same calculation.",
        "never_group": False,
    },

    "significant_figures_wrong": {
        "label": "Significant Figures Wrong",
        "description": "Student gave an answer to an inappropriate number of significant figures — either too many (false precision) or too few (lost accuracy marks).",
        "never_group": False,
    },

    # ==================================================================
    # KINEMATICS & DYNAMICS
    # ==================================================================

    "speed_velocity_confused": {
        "label": "Speed and Velocity Confused",
        "description": "Student used speed and velocity interchangeably — did not recognise that velocity is a vector requiring direction, or omitted direction from a velocity answer.",
        "never_group": False,
    },

    "distance_displacement_confused": {
        "label": "Distance and Displacement Confused",
        "description": "Student confused scalar distance with vector displacement — gave total path length when net displacement was required, or vice versa.",
        "never_group": False,
    },

    "graph_gradient_misread": {
        "label": "Graph Gradient Misread",
        "description": "Student misinterpreted the gradient of a distance-time or velocity-time graph — e.g. read gradient as displacement rather than velocity, or miscalculated the rise/run.",
        "never_group": False,
    },

    "newtons_law_misapplied": {
        "label": "Newton's Law Misapplied",
        "description": "Student applied the wrong Newton's Law — e.g. confused 1st and 2nd law, or applied F=ma without accounting for resultant force.",
        "never_group": False,
    },

    "force_direction_wrong": {
        "label": "Force Direction Wrong",
        "description": "Student identified the correct force but gave the wrong direction — common errors in friction, normal force, or magnetic force direction.",
        "never_group": False,
    },

    "free_body_diagram_wrong": {
        "label": "Free Body Diagram Wrong",
        "description": "Student drew incorrect forces on a free body diagram — missing forces, extra forces, or forces pointing in the wrong direction.",
        "never_group": False,
    },

    # ==================================================================
    # PRESSURE & MOMENTS
    # ==================================================================

    "pressure_formula_wrong": {
        "label": "Pressure Formula Wrong",
        "description": "Student used P = F/A and P = hρg interchangeably without recognising which applies — or substituted values into the wrong formula.",
        "never_group": False,
    },

    "moment_calculation_wrong": {
        "label": "Moment Calculation Wrong",
        "description": "Student calculated moment incorrectly — used slant distance instead of perpendicular distance, or forgot to account for all forces when applying the principle of moments.",
        "never_group": False,
    },

    # ==================================================================
    # ENERGY & POWER
    # ==================================================================

    "energy_type_wrong": {
        "label": "Wrong Energy Type Named",
        "description": "Student identified the wrong type of energy — e.g. confused kinetic and potential energy in a scenario, or called electrical energy 'power'.",
        "never_group": False,
    },

    "energy_conservation_incomplete": {
        "label": "Energy Conservation Incomplete",
        "description": "Student applied conservation of energy but did not account for all energy transfers — e.g. ignored heat lost to friction or sound.",
        "never_group": False,
    },

    "power_efficiency_confused": {
        "label": "Power and Efficiency Confused",
        "description": "Student confused power (rate of energy transfer) with efficiency (ratio of useful output to total input) — used the wrong formula for the question.",
        "never_group": False,
    },

    # ==================================================================
    # WAVES & LIGHT
    # ==================================================================

    "wave_equation_wrong": {
        "label": "Wave Equation Misapplied",
        "description": "Student used v = fλ incorrectly — confused frequency with period, or substituted wavelength in wrong units.",
        "never_group": False,
    },

    "refraction_direction_wrong": {
        "label": "Refraction Direction Wrong",
        "description": "Student drew refracted ray bending the wrong way — e.g. bent away from normal when entering a denser medium, or stated the wrong relationship between speed and refractive index.",
        "never_group": False,
    },

    "total_internal_reflection_condition_missed": {
        "label": "TIR Condition Incomplete",
        "description": "Student described total internal reflection but did not state both conditions — angle must exceed critical angle AND light must travel from denser to less dense medium.",
        "never_group": False,
    },

    # ==================================================================
    # ELECTRICITY & MAGNETISM
    # ==================================================================

    "series_parallel_confused": {
        "label": "Series and Parallel Confused",
        "description": "Student applied series circuit rules to a parallel circuit or vice versa — e.g. added resistances in parallel using series formula, or stated voltage splits equally in parallel.",
        "never_group": False,
    },

    "ohms_law_misapplied": {
        "label": "Ohm's Law Misapplied",
        "description": "Student used V=IR but substituted the wrong V or I — e.g. used supply voltage instead of voltage across a specific component.",
        "never_group": False,
    },

    "magnetic_field_direction_wrong": {
        "label": "Magnetic Field Direction Wrong",
        "description": "Student gave the wrong direction for a magnetic field or force — common errors in Fleming's Left Hand Rule or right-hand grip rule application.",
        "never_group": False,
    },

    "electromagnetic_induction_incomplete": {
        "label": "Induction Explanation Incomplete",
        "description": "Student described electromagnetic induction but did not mention the change in flux or the direction of induced current using Lenz's Law.",
        "never_group": False,
    },

    # ==================================================================
    # RADIOACTIVITY
    # ==================================================================

    "radiation_type_confused": {
        "label": "Radiation Type Confused",
        "description": "Student confused alpha, beta, and gamma radiation — wrong penetrating power, wrong charge, or wrong deflection in a field stated.",
        "never_group": False,
    },

    "half_life_calculation_wrong": {
        "label": "Half-Life Calculation Wrong",
        "description": "Student made an error in a half-life calculation — common errors include dividing by 2 instead of halving repeatedly, or misreading the number of half-lives elapsed.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
