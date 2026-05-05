# mistake_themes/music.py
#
# Mistake themes for Music (O Level / N Level)
# Singapore syllabus — covers listening paper, music theory,
# and written analysis
#
# ──────────────────────────────────────────────────────────────────────
# HOW TO ADD YOUR OWN CATEGORIES  (see base.py for full instructions)
# ──────────────────────────────────────────────────────────────────────

THEMES = {

    "music_term_wrong": {
        "label": "Music Term Used Incorrectly",
        "description": "Student used a music terminology term incorrectly — e.g. confused 'tempo' with 'rhythm', or 'dynamics' with 'pitch'.",
        "never_group": False,
    },

    "music_no_effect": {
        "label": "Effect Not Explained",
        "description": "Student identified a musical feature (e.g. crescendo, staccato) but did not explain the effect it creates on the listener.",
        "never_group": False,
    },

    "music_context_missed": {
        "label": "Musical Context Missed",
        "description": "Student described the music without referencing the historical period, genre, or cultural context that shapes its features.",
        "never_group": False,
    },

    "music_theory_error": {
        "label": "Music Theory Error",
        "description": "Student made an error in a theory question — e.g. wrong key signature, incorrect time value, or error in interval identification.",
        "never_group": False,
    },

    "music_vague_description": {
        "label": "Description Too Vague",
        "description": "Student used non-specific language to describe music (e.g. 'it sounds fast', 'it is loud') without using the correct musical terminology.",
        "never_group": False,
    },

    # ==================================================================
    # TEACHER-ADDED CATEGORIES
    # ==================================================================
}
