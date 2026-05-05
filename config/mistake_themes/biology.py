# mistake_themes/biology.py
#
# Mistake themes for Biology (O Level / N Level)
# Singapore syllabus — covers cell biology, nutrition, transport,
# respiration, excretion, reproduction, genetics, ecology
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    # ==================================================================
    # CELL BIOLOGY
    # ==================================================================

    "mitosis_meiosis_confused": {
        "label": "Mitosis and Meiosis Confused",
        "description": "Student described mitosis when meiosis was required or vice versa — confused the purpose, stages, or products of the two types of cell division.",
        "never_group": False,
    },

    "cell_structure_wrong": {
        "label": "Cell Structure Error",
        "description": "Student incorrectly identified or described a cell organelle — e.g. confused cell wall with cell membrane, or attributed the wrong function to an organelle.",
        "never_group": False,
    },

    "osmosis_diffusion_confused": {
        "label": "Osmosis and Diffusion Confused",
        "description": "Student confused osmosis with diffusion — commonly: described osmosis without specifying water movement across a partially permeable membrane.",
        "never_group": False,
    },

    "osmosis_direction_wrong": {
        "label": "Osmosis Direction Wrong",
        "description": "Student identified osmosis as the process but stated the wrong direction of water movement relative to the concentration gradient.",
        "never_group": False,
    },

    # ==================================================================
    # NUTRITION & ENZYMES
    # ==================================================================

    "enzyme_specificity_missed": {
        "label": "Enzyme Specificity Not Explained",
        "description": "Student described what an enzyme does but did not explain the lock-and-key mechanism or why each enzyme acts on a specific substrate.",
        "never_group": False,
    },

    "enzyme_denatured_vs_inhibited": {
        "label": "Denatured and Inhibited Confused",
        "description": "Student used 'denatured' and 'inhibited' interchangeably — did not distinguish between permanent denaturation (high temp/extreme pH) and reversible inhibition.",
        "never_group": False,
    },

    "nutrient_function_wrong": {
        "label": "Nutrient Function Wrong",
        "description": "Student attributed the wrong function to a nutrient — e.g. stated protein provides energy as its primary role, or confused roles of fat-soluble and water-soluble vitamins.",
        "never_group": False,
    },

    # ==================================================================
    # TRANSPORT
    # ==================================================================

    "photosynthesis_respiration_confused": {
        "label": "Photosynthesis and Respiration Confused",
        "description": "Student confused the reactants and products of photosynthesis and respiration — e.g. stated CO₂ is produced in photosynthesis.",
        "never_group": False,
    },

    "blood_vessel_confused": {
        "label": "Blood Vessel Type Confused",
        "description": "Student confused arteries, veins, and capillaries — wrong structure (e.g. thickness of wall) or wrong direction of blood flow stated.",
        "never_group": False,
    },

    "double_circulation_incomplete": {
        "label": "Double Circulation Incomplete",
        "description": "Student described only one circuit (pulmonary or systemic) when the question required both, or confused which circuit goes to which organ.",
        "never_group": False,
    },

    # ==================================================================
    # RESPIRATION
    # ==================================================================

    "aerobic_anaerobic_confused": {
        "label": "Aerobic and Anaerobic Confused",
        "description": "Student confused aerobic and anaerobic respiration — wrong products, wrong conditions, or wrong ATP yield stated.",
        "never_group": False,
    },

    "respiration_not_combustion": {
        "label": "Respiration Described as Combustion",
        "description": "Student described respiration as 'burning' glucose — did not explain that respiration is a controlled enzymatic process releasing energy in stages.",
        "never_group": False,
    },

    # ==================================================================
    # GENETICS & REPRODUCTION
    # ==================================================================

    "genotype_phenotype_confused": {
        "label": "Genotype and Phenotype Confused",
        "description": "Student confused genotype (allele combination) with phenotype (observable trait) — used the terms interchangeably or reversed them.",
        "never_group": False,
    },

    "dominant_recessive_wrong": {
        "label": "Dominant and Recessive Confused",
        "description": "Student incorrectly identified which allele is dominant or recessive, leading to wrong predicted ratios in genetic crosses.",
        "never_group": False,
    },

    "punnett_square_error": {
        "label": "Punnett Square Error",
        "description": "Student set up or completed the Punnett square incorrectly — wrong gametes on the axes, or miscalculated the resulting genotype ratio.",
        "never_group": False,
    },

    "sex_determination_wrong": {
        "label": "Sex Determination Error",
        "description": "Student made an error in sex-linked inheritance — e.g. forgot that males are XY and have only one X allele, leading to wrong carrier/affected predictions.",
        "never_group": False,
    },

    # ==================================================================
    # ECOLOGY
    # ==================================================================

    "food_chain_direction_wrong": {
        "label": "Food Chain Direction Wrong",
        "description": "Student drew arrows in the wrong direction on a food chain or web — arrows must point in the direction of energy flow, not 'what eats what'.",
        "never_group": False,
    },

    "producer_consumer_confused": {
        "label": "Producer and Consumer Confused",
        "description": "Student incorrectly identified an organism as a producer or consumer — commonly confused decomposers with consumers.",
        "never_group": False,
    },

    "energy_loss_not_explained": {
        "label": "Energy Loss Not Explained",
        "description": "Student stated that energy is lost between trophic levels but did not explain how — e.g. did not mention heat from respiration, excretion, or uneaten parts.",
        "never_group": False,
    },

    # ==================================================================
    # EXAM TECHNIQUE
    # ==================================================================

    "bio_keyword_missing": {
        "label": "Biology Keyword Missing",
        "description": "Student explained the concept correctly but omitted the required biological term that earns the mark — e.g. described osmosis without using 'water potential' or 'partially permeable membrane'.",
        "never_group": False,
    },

    "cause_effect_not_linked": {
        "label": "Cause and Effect Not Linked",
        "description": "Student identified a cause and an effect separately but did not explicitly link them — the logical connection between the two was missing.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # Add your own below this line following the template at the top.
    # ==================================================================
}
