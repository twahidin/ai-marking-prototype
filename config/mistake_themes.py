"""Single source of truth for "Group by Mistake Type" parent themes.

Edit THIS file — and only this file — to add, remove, or rename a theme.
No theme key, label, or description may be hardcoded anywhere else in the
codebase (Python, Jinja, or JS). Consumers either import THEMES directly
or receive the dict injected from the server.

Flags
-----
never_group
    When True, any criterion assigned to this theme is always rendered
    standalone in the student view — it is never bundled into a group
    even if multiple criteria share the theme. The AI is still allowed
    to assign a criterion to this theme; the grouping logic in the
    renderer handles the separation.
"""

THEMES = {
    "reasoning_gap": {
        "label": "Reasoning gap",
        "description": (
            "The student understood the topic but did not fully explain "
            "their thinking — missing consequences, links, or logical steps"
        ),
        "never_group": False,
    },
    "evidence_handling": {
        "label": "Evidence handling",
        "description": (
            "The student did not use, quote, reference, or interpret "
            "evidence or sources correctly"
        ),
        "never_group": False,
    },
    "language_expression": {
        "label": "Language and expression",
        "description": (
            "The student used imprecise, informal, or technically "
            "incorrect language where precision was required"
        ),
        "never_group": False,
    },
    "procedural_error": {
        "label": "Procedural error",
        "description": (
            "The student applied the right concept but used the wrong "
            "method, sequence, or steps"
        ),
        "never_group": False,
    },
    "content_gap": {
        "label": "Content gap",
        "description": (
            "The student did not know the required fact, concept, or "
            "idea — specific to this question"
        ),
        "never_group": True,
    },
}


def theme_keys():
    """Return the list of all theme keys in declaration order."""
    return list(THEMES.keys())


def theme_label(key):
    """Return the display label for a theme key, or the key itself if unknown."""
    return (THEMES.get(key) or {}).get('label', key)


def is_never_group(key):
    """True if criteria with this theme must always render standalone."""
    return bool((THEMES.get(key) or {}).get('never_group'))
