# config/mistake_themes/biology.py
#
# 4 skills-based mistake categories for Biology. Skills, NOT syllabus
# topics — apply equally to cell biology, genetics, ecology, or human
# physiology.

THEMES = {

    "terminology_precision": {
        "label": "Terminology and Keywords",
        "description": "Right idea conveyed but the precise biological keyword is missing or used loosely — e.g. wrote 'food goes through' instead of named processes (peristalsis), or omitted the technical term that earns the mark.",
        "never_group": False,
    },

    "process_explanation": {
        "label": "Process and Mechanism",
        "description": "Student named a process but did not explain it correctly — missing or out-of-sequence steps, missing the structural feature responsible, or describing a mechanism that does not match the named process.",
        "never_group": False,
    },

    "reasoning_gap": {
        "label": "Reasoning Gap",
        "description": "Student stated a fact or observation but did not link it to the consequence the question asked about — missing cause→effect, missing the 'so that' / 'therefore' step, or stopping short of the inference.",
        "never_group": False,
    },

    "content_misconception": {
        "label": "Content Misconception",
        "description": "Student brought the wrong biological concept to the question — confused two structures or processes, applied an idea outside its scope, or held a fundamentally incorrect mental model.",
        "never_group": False,
    },
}
