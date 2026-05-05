# mistake_themes/mathematics.py
#
# Mistake themes for Mathematics (O Level E Math / A Math, N Level)
# Singapore syllabus — covers all topics: arithmetic, algebra, geometry,
# trigonometry, statistics, and calculus (A Math)
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # ARITHMETIC & NUMBER
    # ==================================================================

    "arithmetic_error": {
        "label": "Arithmetic Error",
        "description": "Student used the correct method but made a numerical slip — wrong multiplication, addition, or division in an intermediate step.",
        "never_group": False,
    },

    "rounding_error": {
        "label": "Rounding Error",
        "description": "Student rounded too early in working or rounded to the wrong degree of accuracy — e.g. rounded to 2 d.p. when 3 s.f. was required.",
        "never_group": False,
    },

    "unit_conversion_error": {
        "label": "Unit Conversion Error",
        "description": "Student used inconsistent units or failed to convert — e.g. mixed cm and m, or forgot to convert minutes to hours.",
        "never_group": False,
    },

    "unit_omitted": {
        "label": "Unit Omitted",
        "description": "Correct numerical answer given but units missing from the final answer — e.g. wrote 45 instead of 45 cm².",
        "never_group": False,
    },

    # ==================================================================
    # ALGEBRA
    # ==================================================================

    "wrong_formula_used": {
        "label": "Wrong Formula Used",
        "description": "Student applied a formula that does not fit the question — e.g. used simple interest formula for compound interest, or wrong trigonometric ratio.",
        "never_group": False,
    },

    "formula_misapplied": {
        "label": "Formula Misapplied",
        "description": "Student identified the correct formula but substituted values incorrectly or rearranged it wrongly before substituting.",
        "never_group": False,
    },

    "sign_error": {
        "label": "Sign Error",
        "description": "Student made an error involving positive/negative signs — e.g. subtracted instead of added when expanding brackets, or dropped a negative.",
        "never_group": False,
    },

    "expansion_error": {
        "label": "Expansion Error",
        "description": "Student made an error when expanding brackets — e.g. forgot to multiply all terms, or incorrectly expanded a perfect square.",
        "never_group": False,
    },

    "factorisation_error": {
        "label": "Factorisation Error",
        "description": "Student attempted to factorise but found incorrect factors — common error in quadratic factorisation or difference of two squares.",
        "never_group": False,
    },

    "simultaneous_equation_error": {
        "label": "Simultaneous Equation Error",
        "description": "Student set up or solved simultaneous equations incorrectly — common errors include wrong elimination step or substitution mistake.",
        "never_group": False,
    },

    # ==================================================================
    # GEOMETRY & MEASUREMENT
    # ==================================================================

    "wrong_property_used": {
        "label": "Wrong Geometry Property",
        "description": "Student applied an incorrect geometric property or theorem — e.g. confused alternate angles with co-interior angles, or used the wrong circle theorem.",
        "never_group": False,
    },

    "property_not_stated": {
        "label": "Property Not Stated",
        "description": "Student gave the correct angle or length but did not state the geometric reason or property — losing the reasoning mark.",
        "never_group": False,
    },

    "area_perimeter_confusion": {
        "label": "Area and Perimeter Confused",
        "description": "Student calculated area when perimeter was asked, or vice versa — fundamental confusion between the two measures.",
        "never_group": False,
    },

    "composite_shape_error": {
        "label": "Composite Shape Error",
        "description": "Student incorrectly split or combined a composite shape — e.g. double-counted a region, or subtracted the wrong part.",
        "never_group": False,
    },

    # ==================================================================
    # TRIGONOMETRY
    # ==================================================================

    "trig_ratio_wrong": {
        "label": "Wrong Trig Ratio",
        "description": "Student used sin when cos or tan was required — confused which ratio applies to the given sides or angle.",
        "never_group": False,
    },

    "bearing_error": {
        "label": "Bearing Error",
        "description": "Student measured bearing from the wrong direction or reference point — e.g. measured from south instead of north.",
        "never_group": False,
    },

    "ambiguous_case_missed": {
        "label": "Ambiguous Case Missed",
        "description": "Student found one solution for a trigonometric equation but missed the second valid solution in the required range.",
        "never_group": False,
    },

    # ==================================================================
    # GRAPHS & STATISTICS
    # ==================================================================

    "graph_not_labelled": {
        "label": "Graph Not Labelled",
        "description": "Student drew the correct graph but omitted axis labels, scale, units, or the graph title — losing presentation marks.",
        "never_group": False,
    },

    "graph_reading_error": {
        "label": "Graph Reading Error",
        "description": "Student misread a value from a graph — incorrect interpolation, misread scale, or read from wrong axis.",
        "never_group": False,
    },

    "statistics_measure_confused": {
        "label": "Mean / Median / Mode Confused",
        "description": "Student calculated the wrong measure of central tendency — computed mean when median was asked, or vice versa.",
        "never_group": False,
    },

    # ==================================================================
    # CALCULUS (A Math)
    # ==================================================================

    "differentiation_error": {
        "label": "Differentiation Error",
        "description": "Student differentiated incorrectly — common errors include wrong chain rule application, or forgetting to bring down the power.",
        "never_group": False,
    },

    "integration_error": {
        "label": "Integration Error",
        "description": "Student integrated incorrectly — common errors include forgetting the constant of integration, or wrong limits substitution.",
        "never_group": False,
    },

    "stationary_point_error": {
        "label": "Stationary Point Error",
        "description": "Student found the stationary point but did not determine its nature (max/min), or used the wrong method to determine it.",
        "never_group": False,
    },

    # ==================================================================
    # EXAM TECHNIQUE
    # ==================================================================

    "answer_not_in_required_form": {
        "label": "Answer Not in Required Form",
        "description": "Student gave a correct value but not in the form asked — e.g. left as an improper fraction when a mixed number was required, or did not simplify surd.",
        "never_group": False,
    },

    "negative_answer_rejected": {
        "label": "Negative Answer Not Rejected",
        "description": "Student obtained a negative value for a length, area, or other quantity that must be positive but did not reject it or explain why.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
