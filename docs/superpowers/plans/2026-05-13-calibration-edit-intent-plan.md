# Calibration edit intent + subject standards — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single "Save to calibration bank" toggle with two explicit intent checkboxes (`Amend answer key` / `Update subject standards`), introduce a topic-scoped `SubjectStandard` bank that scales year-over-year, decouple the assignment-bank version from local amendments, and hide the teacher-facing theme_key correction UI. Spec: `docs/superpowers/specs/2026-05-13-calibration-edit-intent-design.md`.

**Architecture:** New SQL tables (`SubjectStandard`, `SubjectTopicVocabulary`, `SubjectLead`) sit alongside the existing `FeedbackEdit` table. Existing `FeedbackEdit` gets two new columns (`amend_answer_key`, `promoted_to_subject_standard_id`). The existing calibration retrieval (`fetch_calibration_examples`, `build_calibration_block`) is replaced by per-topic SubjectStandard retrieval plus per-assignment amendment merge. AI topic tagging at assignment creation uses Haiku via the existing `_helper_model_for` pattern. The new "Subject standards" page (Settings) provides review queue and inline-edit. Migration is a clean break with a 5-day in-flight cutoff.

**Tech Stack:** Flask + SQLAlchemy (existing), Anthropic + OpenAI SDKs (existing), Jinja2 templates, vanilla JS in `static/js/feedback_render.js`, pytest with SQLite test DB (existing `tests/conftest.py` pattern).

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `config/subject_topics/__init__.py` | Per-subject topic vocabulary registry and lookup helpers |
| `config/subject_topics/biology.py` | Biology topic keys |
| `config/subject_topics/chemistry.py` | Chemistry topic keys |
| `config/subject_topics/physics.py` | Physics topic keys |
| `config/subject_topics/mathematics.py` | Mathematics topic keys |
| `config/subject_topics/english.py` | English topic keys (skill-style) |
| `config/subject_topics/lower_secondary_science.py` | Sec 1–2 science topic keys |
| `config/subject_topics/history.py` | History topic keys |
| `config/subject_topics/geography.py` | Geography topic keys |
| `subject_standards.py` | Domain module: SubjectStandard retrieval, promotion, dedup, vocabulary access. Keep app.py / ai_marking.py thin. |
| `templates/subject_standards.html` | Standards page UI: pending queue + active list + filters + inline edit |
| `tests/test_calibration_intent.py` | Two-checkbox save routing + amendment merge |
| `tests/test_subject_standards.py` | SubjectStandard CRUD, dedup, retrieval, permission |
| `tests/test_subject_topic_vocab.py` | Vocabulary loading and lookup |
| `tests/test_migration_calibration.py` | 5-day cutoff classification + legacy deactivation |
| `tests/test_bank_amendment_push.py` | Bank push with optimistic concurrency |
| `tests/test_rubric_reupload.py` | Amendment auto-carry on rubric re-upload |

**Modified files:**

| Path | What changes |
|---|---|
| `db.py` | New models: `SubjectStandard`, `SubjectTopicVocabulary`, `SubjectLead`. New columns on `FeedbackEdit` (`amend_answer_key`, `promoted_to_subject_standard_id`) and `Assignment` (`topic_keys`, `topic_keys_status`, `bank_pushed_at`). `_migrate_add_columns` extended with the new columns and the 5-day-cutoff one-shot backfill. |
| `ai_marking.py` | New `extract_assignment_topic_keys` and `extract_standard_topic_keys` helpers (Haiku). Replace `fetch_calibration_examples` + `build_calibration_block` with new subject-standards retrieval (delegates to `subject_standards.py`). Modify `_build_rubrics_prompt` and `_build_short_answer_prompt` to inject "Teacher clarifications" section AND "Subject standards" section. |
| `app.py` | Modify `_process_text_edit` to accept `amend_answer_key` + `update_subject_standards` instead of `calibrate`. Modify `teacher_submission_result_patch` to read the two new fields from request JSON. Add routes: `/teacher/subject-standards`, `/api/subject_standards/...`, `/teacher/assignment/<id>/push-amendments-to-bank`, `/teacher/assignment/<id>/answer-key-preview`. Add `_can_edit_subject_standards()` ACL helper. Add lazy topic-tagging trigger on assignment open. Hook rubric re-upload to call amendment carry-over. |
| `static/js/feedback_render.js` | Replace single "Save to calibration bank" checkbox with two checkboxes ("Amend answer key", "Update subject standards"). Pass the two booleans in the PATCH payload. Gate the theme_key dropdown render on `window.TEACHER_THEME_UI_ENABLED`. |
| `templates/base.html` | Inject `TEACHER_THEME_UI_ENABLED` flag into the JS global. Add Settings → "Subject standards" link (visible only to HOD / subject lead). |
| `templates/class.html` (or assignment detail template) | Add "Topic tags" section, "Effective answer key" preview, "Update bank version with my amendments" button, "needs manual tagging" badge, rubric-reupload banner. |
| `subjects.py` | No code change; the module already exists. Confirm `resolve_subject_key` returns canonical keys we can join SubjectStandard.subject on. |

**Removed (after migration grace window):**
- `MarkingPrinciplesCache` writes (table preserved; reads return empty). Reads in `ai_marking.py:get_marking_principles` and `build_calibration_block` are replaced.

---

## Phase 0: Pre-flight + branch confirmation

### Task 0.1: Confirm working branch

- [ ] **Step 1: Verify branch**

```bash
git branch --show-current
```

Expected: `sandbox_upgraded`

- [ ] **Step 2: Verify spec is committed**

```bash
git log --oneline -- docs/superpowers/specs/2026-05-13-calibration-edit-intent-design.md
```

Expected: at least one commit listed (e.g., `f47bf74 docs(spec): calibration edit intent ...`).

- [ ] **Step 3: Verify clean tree before starting**

```bash
git status --short
```

Expected: empty output apart from the plan file itself.

---

## Phase 1: Schema changes

### Task 1.1: Add new columns to `FeedbackEdit` and `Assignment`

**Files:**
- Modify: `db.py` — extend `_migrate_add_columns` (around line 67) and the model classes

- [ ] **Step 1: Write a failing migration test**

Create `tests/test_migration_calibration.py`:

```python
"""UP-: schema migration for calibration edit intent + subject standards."""

from sqlalchemy import inspect
from db import db


def test_feedback_edit_has_amend_answer_key_column(app):
    with app.app_context():
        cols = [c['name'] for c in inspect(db.engine).get_columns('feedback_edit')]
        assert 'amend_answer_key' in cols
        assert 'promoted_to_subject_standard_id' in cols


def test_assignment_has_topic_keys_columns(app):
    with app.app_context():
        cols = [c['name'] for c in inspect(db.engine).get_columns('assignments')]
        assert 'topic_keys' in cols
        assert 'topic_keys_status' in cols
        assert 'bank_pushed_at' in cols
```

- [ ] **Step 2: Run the test — expect FAIL**

```bash
pytest tests/test_migration_calibration.py -v
```

Expected: both tests fail with "column not found" assertions.

- [ ] **Step 3: Add the columns to `FeedbackEdit` class definition**

In `db.py`, inside `class FeedbackEdit` (around line 942–979), add:

```python
    # New (calibration intent design 2026-05-13)
    amend_answer_key = db.Column(db.Boolean, nullable=False, default=False)
    promoted_to_subject_standard_id = db.Column(
        db.Integer,
        db.ForeignKey('subject_standard.id'),
        nullable=True,
        index=True,
    )
```

- [ ] **Step 4: Add the columns to `Assignment` class definition**

In `db.py`, inside `class Assignment` (around line 684–750), before the `students` relationship, add:

```python
    # New (calibration intent design 2026-05-13)
    topic_keys = db.Column(db.Text, default='[]', nullable=False)
    topic_keys_status = db.Column(db.String(20), default='pending', nullable=False)
    bank_pushed_at = db.Column(db.DateTime(timezone=True), nullable=True)
```

- [ ] **Step 5: Extend `_migrate_add_columns` to ALTER existing tables**

In `db.py`, inside `_migrate_add_columns`, add a new block:

```python
        if 'feedback_edit' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('feedback_edit')]
            if 'amend_answer_key' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN amend_answer_key BOOLEAN DEFAULT FALSE NOT NULL'))
                db.session.commit()
                logger.info('Added amend_answer_key column to feedback_edit table')
            if 'promoted_to_subject_standard_id' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN promoted_to_subject_standard_id INTEGER'))
                db.session.commit()
                logger.info('Added promoted_to_subject_standard_id column to feedback_edit table')

        if 'assignments' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('assignments')]
            if 'topic_keys' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN topic_keys TEXT DEFAULT '[]' NOT NULL"))
                db.session.commit()
                logger.info('Added topic_keys column to assignments table')
            if 'topic_keys_status' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN topic_keys_status VARCHAR(20) DEFAULT 'pending' NOT NULL"))
                db.session.commit()
                logger.info('Added topic_keys_status column to assignments table')
            if 'bank_pushed_at' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN bank_pushed_at TIMESTAMP"))
                db.session.commit()
                logger.info('Added bank_pushed_at column to assignments table')
```

- [ ] **Step 6: Run the test — expect PASS**

```bash
pytest tests/test_migration_calibration.py -v
```

- [ ] **Step 7: Commit**

```bash
git add db.py tests/test_migration_calibration.py
git commit -m "feat(db): add amend_answer_key + topic_keys columns for calibration intent design"
```

### Task 1.2: Add `SubjectStandard` model

**Files:**
- Modify: `db.py` — new class before `MarkingPrinciplesCache`

- [ ] **Step 1: Write failing model test**

Append to `tests/test_migration_calibration.py`:

```python
def test_subject_standard_table_exists(app):
    with app.app_context():
        names = inspect(db.engine).get_table_names()
        assert 'subject_standard' in names
        cols = {c['name'] for c in inspect(db.engine).get_columns('subject_standard')}
        for required in (
            'id', 'uuid', 'subject', 'text', 'topic_keys', 'theme_key',
            'reinforcement_count', 'status', 'created_by',
            'created_at', 'updated_at', 'last_seen_at',
            'reviewed_by', 'reviewed_at',
            'source_feedback_edit_ids', 'metadata_json',
        ):
            assert required in cols, f"missing column {required}"


def test_subject_standard_insert_round_trip(app, db_session):
    from db import SubjectStandard
    s = SubjectStandard(
        subject='biology',
        text='Accept "temperature" but reject "heat".',
        topic_keys='["enzymes", "terminology_precision"]',
        theme_key='terminology_precision',
        status='pending_review',
        created_by='teacher-1',
    )
    db_session.add(s)
    db_session.commit()
    fetched = SubjectStandard.query.filter_by(subject='biology').first()
    assert fetched is not None
    assert fetched.uuid  # auto-generated
    assert fetched.reinforcement_count == 1
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add `SubjectStandard` class to `db.py`**

Insert before `class MarkingPrinciplesCache`:

```python
class SubjectStandard(db.Model):
    """Subject-wide marking standard, promoted from a teacher's FeedbackEdit
    with the "Update subject standards" intent. Topic-scoped retrieval pulls
    these into the marking prompt when the assignment's topics overlap.
    See docs/superpowers/specs/2026-05-13-calibration-edit-intent-design.md.
    """
    __tablename__ = 'subject_standard'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    uuid = db.Column(
        db.String(40),
        unique=True,
        nullable=False,
        default=lambda: 'ss_' + str(__import__('uuid').uuid4()),
    )
    subject = db.Column(db.String(80), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    topic_keys = db.Column(db.Text, nullable=False, default='[]')
    theme_key = db.Column(db.String(64), nullable=True)
    reinforcement_count = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default='pending_review')
    created_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    reviewed_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    source_feedback_edit_ids = db.Column(db.Text, nullable=False, default='[]')
    metadata_json = db.Column(db.Text, nullable=False, default='{}')

    __table_args__ = (
        db.Index('ix_subject_standard_subject_status', 'subject', 'status'),
    )
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_migration_calibration.py
git commit -m "feat(db): add SubjectStandard model for topic-scoped marking standards"
```

### Task 1.3: Add `SubjectTopicVocabulary` and `SubjectLead` models

- [ ] **Step 1: Add failing tests**

Append to `tests/test_migration_calibration.py`:

```python
def test_subject_topic_vocabulary_table_exists(app):
    with app.app_context():
        names = inspect(db.engine).get_table_names()
        assert 'subject_topic_vocabulary' in names


def test_subject_lead_table_exists(app):
    with app.app_context():
        names = inspect(db.engine).get_table_names()
        assert 'subject_lead' in names


def test_subject_topic_vocabulary_round_trip(app, db_session):
    from db import SubjectTopicVocabulary
    v = SubjectTopicVocabulary(subject='biology', topic_key='enzymes', display_name='Enzymes')
    db_session.add(v)
    db_session.commit()
    got = SubjectTopicVocabulary.query.filter_by(subject='biology', topic_key='enzymes').first()
    assert got is not None
    assert got.display_name == 'Enzymes'
    assert got.active is True
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add models to `db.py`**

```python
class SubjectTopicVocabulary(db.Model):
    """Allowed topic keys per subject. Seeded from config/subject_topics/<subject>.py
    on first boot, mutable thereafter by HOD/subject leads via the UI."""
    __tablename__ = 'subject_topic_vocabulary'
    subject = db.Column(db.String(80), primary_key=True)
    topic_key = db.Column(db.String(64), primary_key=True)
    display_name = db.Column(db.String(200), nullable=False, default='')
    active = db.Column(db.Boolean, nullable=False, default=True)


class SubjectLead(db.Model):
    """Per-(teacher, subject) flag: this teacher can manage subject standards
    for this subject. HOD role grants this implicitly for all subjects."""
    __tablename__ = 'subject_lead'
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'), primary_key=True)
    subject = db.Column(db.String(80), primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_migration_calibration.py
git commit -m "feat(db): add SubjectTopicVocabulary + SubjectLead models"
```

---

## Phase 2: Topic vocabulary config

### Task 2.1: Create `config/subject_topics` package

**Files:**
- Create: `config/subject_topics/__init__.py` and all 8 subject files
- Test: `tests/test_subject_topic_vocab.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_subject_topic_vocab.py`:

```python
"""UP-: subject topic vocabulary loading and per-subject lookup."""

from config.subject_topics import (
    get_topics_for_subject,
    is_known_topic_key,
    SUBJECTS_WITH_VOCAB,
)


def test_biology_topics_include_enzymes():
    topics = get_topics_for_subject('biology')
    keys = [k for k, _ in topics]
    assert 'enzymes' in keys
    assert 'terminology_precision' in keys


def test_unknown_subject_returns_empty_list():
    assert get_topics_for_subject('underwater_basketweaving') == []


def test_is_known_topic_key_positive():
    assert is_known_topic_key('biology', 'enzymes') is True


def test_is_known_topic_key_negative_unknown():
    assert is_known_topic_key('biology', 'flux_capacitor') is False


def test_subjects_with_vocab_lists_canonical_keys():
    assert 'biology' in SUBJECTS_WITH_VOCAB
    assert 'chemistry' in SUBJECTS_WITH_VOCAB
    assert 'mathematics' in SUBJECTS_WITH_VOCAB
```

- [ ] **Step 2: Run — expect FAIL (ImportError)**

- [ ] **Step 3: Create `config/subject_topics/__init__.py`**

```python
"""Per-subject topic vocabulary registry.

Each subject has its own module (e.g. `biology.py`) defining a `TOPICS` list
of `(topic_key, display_name)` tuples. This package exposes lookup helpers
used by the AI tagger, the marking-time retrieval, and the standards page UI.

Adding a subject: create config/subject_topics/<subject>.py with TOPICS, then
add the module name to `_SUBJECT_MODULES` below. Subject keys MUST match the
canonical keys in subjects.py.
"""
import importlib
from typing import Tuple, List

_SUBJECT_MODULES = (
    'biology',
    'chemistry',
    'physics',
    'mathematics',
    'english',
    'lower_secondary_science',
    'history',
    'geography',
)


def _load_topics(subject_key: str) -> List[Tuple[str, str]]:
    try:
        mod = importlib.import_module(f'config.subject_topics.{subject_key}')
    except ModuleNotFoundError:
        return []
    return list(getattr(mod, 'TOPICS', []))


SUBJECTS_WITH_VOCAB = tuple(_SUBJECT_MODULES)


def get_topics_for_subject(subject_key: str) -> List[Tuple[str, str]]:
    return _load_topics(subject_key)


def is_known_topic_key(subject_key: str, topic_key: str) -> bool:
    return any(k == topic_key for k, _ in _load_topics(subject_key))


def get_display_name(subject_key: str, topic_key: str) -> str:
    for k, label in _load_topics(subject_key):
        if k == topic_key:
            return label
    return topic_key
```

- [ ] **Step 4: Create `config/subject_topics/biology.py`**

```python
"""Biology topic vocabulary."""

TOPICS = [
    ('cell_structure', 'Cell structure'),
    ('cellular_respiration', 'Cellular respiration'),
    ('photosynthesis', 'Photosynthesis'),
    ('enzymes', 'Enzymes'),
    ('genetics', 'Genetics'),
    ('inheritance', 'Inheritance'),
    ('evolution', 'Evolution'),
    ('ecology', 'Ecology and environment'),
    ('homeostasis', 'Homeostasis'),
    ('digestion', 'Digestion and nutrition'),
    ('respiration_breathing', 'Breathing and gas exchange'),
    ('circulatory_system', 'Circulatory system'),
    ('nervous_system', 'Nervous system'),
    ('reproduction', 'Reproduction'),
    ('transport_in_plants', 'Transport in plants'),
    ('microorganisms', 'Microorganisms and disease'),
    # Cross-cutting skill-style
    ('terminology_precision', 'Terminology precision'),
    ('units', 'Units'),
    ('diagram_labelling', 'Diagram labelling'),
    ('calculation', 'Calculation'),
    ('experimental_method', 'Experimental method'),
    ('data_interpretation', 'Data interpretation'),
]
```

- [ ] **Step 5: Create the other 7 subject vocab files**

Use the same `TOPICS = [(key, display), ...]` shape. Each file must be importable. Suggested per-subject vocab:

- **`chemistry.py`** (~22 keys): `atomic_structure, periodic_table, bonding_structure, stoichiometry, mole_concept, acids_bases_salts, redox, electrochemistry, energetics, kinetics, equilibrium, organic_chemistry, polymers, qualitative_analysis, terminology_precision, units, balanced_equations, calculation, experimental_method, observations, significant_figures, data_interpretation`
- **`physics.py`** (~22 keys): `kinematics, dynamics, forces_pressure, energy_work_power, thermal_physics, waves, light, sound, electricity, magnetism, electromagnetic_induction, radioactivity, modern_physics, terminology_precision, units, vector_notation, diagram_drawing, calculation, experimental_method, graph_interpretation, significant_figures, data_interpretation`
- **`mathematics.py`** (~19 keys): `algebra_basics, equations_inequalities, functions_graphs, coordinate_geometry, trigonometry, mensuration, geometry_proofs, statistics, probability, sequences_series, logarithms_exponents, vectors, calculus_differentiation, calculus_integration, procedural_execution, method_choice, justification, units, significant_figures`
- **`english.py`** (~12 skill-style keys): `comprehension_inference, character_analysis, theme_interpretation, language_devices, vocabulary_in_context, narrative_voice, evidence_use, argument_structure, expression_precision, register, paragraphing, summary_skills`
- **`lower_secondary_science.py`** (~22 keys): subset / merge of biology + chemistry + physics + same cross-cutting keys
- **`history.py`** (~15 keys): a mix of period-style and skill-style — examples: `industrial_revolution, world_war_one, world_war_two, cold_war, decolonisation, singapore_independence, source_analysis, argument_structure, evidence_use, terminology_precision, dates_and_chronology, ...`
- **`geography.py`** (~15 keys): examples: `weather_climate, rivers, coasts, plate_tectonics, ecosystems, urbanisation, food_security, tourism, terminology_precision, map_skills, data_interpretation, fieldwork_method, ...`

Each file follows the exact pattern of `biology.py`.

- [ ] **Step 6: Run — expect PASS**

```bash
pytest tests/test_subject_topic_vocab.py -v
```

- [ ] **Step 7: Commit**

```bash
git add config/subject_topics/ tests/test_subject_topic_vocab.py
git commit -m "feat(config): topic vocabularies per canonical subject"
```

### Task 2.2: Seed `SubjectTopicVocabulary` from config on boot

- [ ] **Step 1: Add failing test**

Append to `tests/test_subject_topic_vocab.py`:

```python
def test_subject_topic_vocab_seeded_on_boot(app, db_session):
    from db import SubjectTopicVocabulary
    rows = SubjectTopicVocabulary.query.filter_by(subject='biology').all()
    keys = {r.topic_key for r in rows}
    assert 'enzymes' in keys
    assert 'terminology_precision' in keys
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Create `subject_standards.py` with the seed function**

Create at repo root:

```python
"""Domain logic for SubjectStandard: vocab seeding, retrieval, promotion,
dedup. Imported by app.py and ai_marking.py; should not import from either
to avoid cycles.
"""
import json
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher

from config.subject_topics import get_topics_for_subject, SUBJECTS_WITH_VOCAB
from db import db, FeedbackEdit, SubjectStandard, SubjectTopicVocabulary

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.85
PER_TOPIC_QUOTA = 3
ABSOLUTE_CAP = 30


def seed_subject_topic_vocabulary():
    """Idempotent: inserts missing (subject, topic_key) rows from
    config/subject_topics/*. Existing rows are left untouched."""
    for subject_key in SUBJECTS_WITH_VOCAB:
        for topic_key, display in get_topics_for_subject(subject_key):
            exists = SubjectTopicVocabulary.query.filter_by(
                subject=subject_key,
                topic_key=topic_key,
            ).first()
            if exists is None:
                db.session.add(SubjectTopicVocabulary(
                    subject=subject_key,
                    topic_key=topic_key,
                    display_name=display,
                    active=True,
                ))
    db.session.commit()
```

- [ ] **Step 4: Call from `init_db`**

In `db.py`, inside `init_db`, after `db.create_all()` and after `_migrate_add_columns(app)`, add:

```python
        from subject_standards import seed_subject_topic_vocabulary
        seed_subject_topic_vocabulary()
```

- [ ] **Step 5: Run — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add db.py subject_standards.py tests/test_subject_topic_vocab.py
git commit -m "feat(boot): seed SubjectTopicVocabulary from config on init_db"
```

---

## Phase 3: AI topic tagger

### Task 3.1: `extract_assignment_topic_keys` helper

**Files:**
- Modify: `ai_marking.py`
- Test: `tests/test_subject_standards.py`

- [ ] **Step 1: Create failing test**

Create `tests/test_subject_standards.py`:

```python
"""UP-: AI topic tagging + SubjectStandard retrieval / promotion."""

from unittest.mock import patch


def test_extract_assignment_topic_keys_returns_list_per_question(app):
    from ai_marking import extract_assignment_topic_keys
    import json
    fake_response = {
        'questions': [
            {'question_num': 1, 'topic_keys': ['enzymes', 'terminology_precision']},
            {'question_num': 2, 'topic_keys': ['cellular_respiration']},
        ]
    }
    with app.app_context():
        with patch('ai_marking._simple_completion', return_value=json.dumps(fake_response)):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[
                    {'question_num': 1, 'text': 'State one factor affecting enzyme activity.', 'answer_key': 'temperature, pH'},
                    {'question_num': 2, 'text': 'Explain ATP production.', 'answer_key': 'mitochondria, ATP synthase'},
                ],
            )
    assert result == [
        ['enzymes', 'terminology_precision'],
        ['cellular_respiration'],
    ]


def test_extract_assignment_topic_keys_filters_unknown_keys(app):
    from ai_marking import extract_assignment_topic_keys
    import json
    fake_response = {
        'questions': [
            {'question_num': 1, 'topic_keys': ['enzymes', 'flux_capacitor']},
        ]
    }
    with app.app_context():
        with patch('ai_marking._simple_completion', return_value=json.dumps(fake_response)):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[{'question_num': 1, 'text': 'x', 'answer_key': 'y'}],
            )
    assert result == [['enzymes']]


def test_extract_assignment_topic_keys_returns_empty_on_failure(app):
    from ai_marking import extract_assignment_topic_keys
    with app.app_context():
        with patch('ai_marking._simple_completion', side_effect=Exception('network')):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[{'question_num': 1, 'text': 'x', 'answer_key': 'y'}],
            )
    assert result == [[]]
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add `extract_assignment_topic_keys` to `ai_marking.py`**

After `categorise_mistakes` (around line 2444), add:

```python
def extract_assignment_topic_keys(provider, model, session_keys, subject, questions, max_retries=3):
    """For each question, return a list of topic_keys drawn from the subject's
    controlled vocabulary. Returns [[], [], ...] (one empty list per question)
    if the AI call fails after `max_retries`.

    `questions` is a list of {'question_num', 'text', 'answer_key'} dicts.
    """
    from config.subject_topics import get_topics_for_subject, is_known_topic_key

    vocab = get_topics_for_subject(subject)
    if not vocab:
        return [[] for _ in questions]
    vocab_lines = '\n'.join(f'  - {k}: {label}' for k, label in vocab)

    lines = [
        f'Subject: {subject}',
        '',
        'Controlled vocabulary (you MUST pick from this list):',
        vocab_lines,
        '',
        'Tag each question with 1–3 topic keys from the vocab above. Pick keys that capture both content domain (e.g. enzymes) and cross-cutting skill (e.g. terminology_precision) where applicable.',
        '',
        'Questions:',
    ]
    for q in questions:
        lines.append(f"Q{q['question_num']}: {q.get('text', '')}")
        if q.get('answer_key'):
            lines.append(f"  Answer key: {q['answer_key']}")
    lines.append('')
    lines.append('Return JSON only: {"questions": [{"question_num": N, "topic_keys": ["..."]}, ...]}')
    user_prompt = '\n'.join(lines)
    system_prompt = 'You are a topic-tagger for school assignments. Output JSON only, no commentary.'

    last_err = None
    for attempt in range(max_retries):
        try:
            raw = _simple_completion(
                provider=provider, model=model, session_keys=session_keys,
                system=system_prompt, user=user_prompt, max_tokens=1200,
            )
            parsed = parse_ai_response(raw)
            out = []
            for q in questions:
                qn = q['question_num']
                match = next(
                    (item for item in parsed.get('questions', []) if str(item.get('question_num')) == str(qn)),
                    None,
                )
                keys = (match or {}).get('topic_keys') or []
                filtered = []
                for k in keys:
                    if is_known_topic_key(subject, k):
                        filtered.append(k)
                    else:
                        logger.info(f'extract_assignment_topic_keys: dropped unknown key {k!r} for subject {subject}')
                out.append(filtered[:3])
            return out
        except Exception as e:
            last_err = e
            logger.warning(f'extract_assignment_topic_keys attempt {attempt + 1} failed: {e}')

    logger.error(f'extract_assignment_topic_keys gave up after {max_retries} attempts: {last_err}')
    return [[] for _ in questions]
```

**Important:** The `_simple_completion` function at line 1668 expects `session=` not `session_keys=`. Inspect the existing signature and pass the kwarg name that matches. If you change the kwarg in your test mock, change it consistently.

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/test_subject_standards.py -v
```

- [ ] **Step 5: Commit**

```bash
git add ai_marking.py tests/test_subject_standards.py
git commit -m "feat(ai): extract_assignment_topic_keys helper with retry + vocab filter"
```

### Task 3.2: `extract_standard_topic_keys` for promotion-time tagging

- [ ] **Step 1: Add failing test**

Append:

```python
def test_extract_standard_topic_keys_from_edit(app):
    from ai_marking import extract_standard_topic_keys
    import json
    fake_response = {'topic_keys': ['enzymes', 'terminology_precision']}
    with app.app_context():
        with patch('ai_marking._simple_completion', return_value=json.dumps(fake_response)):
            keys = extract_standard_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                question_text='State one factor affecting enzyme activity.',
                original_feedback='Correct — heat affects enzyme rate.',
                edited_feedback="Must say 'temperature', not 'heat'.",
                theme_key='terminology_precision',
            )
    assert keys == ['enzymes', 'terminology_precision']
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add helper**

```python
def extract_standard_topic_keys(provider, model, session_keys, subject,
                                 question_text, original_feedback, edited_feedback,
                                 theme_key=None, max_retries=3):
    """Tag a teacher edit (being promoted) with 1–3 topic keys from the
    controlled vocabulary. Returns [] on failure."""
    from config.subject_topics import get_topics_for_subject, is_known_topic_key

    vocab = get_topics_for_subject(subject)
    if not vocab:
        return []
    vocab_lines = '\n'.join(f'  - {k}: {label}' for k, label in vocab)

    user_prompt = (
        f'Subject: {subject}\n'
        '\nControlled vocabulary (you MUST pick from this list):\n'
        f'{vocab_lines}\n\n'
        f'Question being marked: {question_text}\n'
        f'Original AI feedback: {original_feedback}\n'
        f"Teacher's correction: {edited_feedback}\n"
        f'Theme of mistake: {theme_key or "(not categorised)"}\n\n'
        'Tag this correction with 1–3 topic keys that describe the content domain '
        'AND the type of skill the correction targets. Pick from the vocab only.\n\n'
        'Return JSON only: {"topic_keys": ["..."]}'
    )
    system_prompt = 'You are a topic-tagger for teacher feedback edits. Output JSON only.'

    last_err = None
    for attempt in range(max_retries):
        try:
            raw = _simple_completion(
                provider=provider, model=model, session_keys=session_keys,
                system=system_prompt, user=user_prompt, max_tokens=200,
            )
            parsed = parse_ai_response(raw)
            keys = parsed.get('topic_keys', []) or []
            filtered = [k for k in keys if is_known_topic_key(subject, k)]
            return filtered[:3]
        except Exception as e:
            last_err = e
            logger.warning(f'extract_standard_topic_keys attempt {attempt + 1} failed: {e}')
    logger.error(f'extract_standard_topic_keys gave up: {last_err}')
    return []
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add ai_marking.py tests/test_subject_standards.py
git commit -m "feat(ai): extract_standard_topic_keys for promotion-time tagging"
```

---

## Phase 4: Subject standards domain logic

### Task 4.1: `find_similar_standard` and `promote_to_subject_standard`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_subject_standards.py`:

```python
def test_promote_creates_new_standard_when_no_similar_exists(app, db_session):
    from db import SubjectStandard, Teacher, Assignment, Submission, FeedbackEdit
    from subject_standards import promote_to_subject_standard
    from unittest.mock import patch
    import json

    t = Teacher(id='t-1', name='Joe', access_code='JOE1', role='teacher')
    db_session.add(t)
    asn = Assignment(id='a-1', classroom_code='ABC123', subject='biology', title='Bio Test')
    db_session.add(asn)
    db_session.commit()
    sub = Submission(assignment_id=asn.id, student_id=None, result_json='{}')
    db_session.add(sub)
    db_session.commit()
    fe = FeedbackEdit(
        submission_id=sub.id, criterion_id='1', field='feedback',
        original_text='Correct — heat affects enzyme rate.',
        edited_text="Must say 'temperature', not 'heat'.",
        edited_by=t.id, theme_key='terminology_precision',
        assignment_id=asn.id, rubric_version='v1', scope='promoted', active=True,
    )
    db_session.add(fe)
    db_session.commit()

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes', 'terminology_precision']):
        ss_id = promote_to_subject_standard(
            feedback_edit_id=fe.id,
            provider='anthropic', model='claude-haiku-4-5',
            session_keys={'anthropic': 'sk-fake'},
        )

    ss = SubjectStandard.query.get(ss_id)
    assert ss is not None
    assert ss.subject == 'biology'
    assert ss.status == 'pending_review'
    assert ss.reinforcement_count == 1
    assert json.loads(ss.topic_keys) == ['enzymes', 'terminology_precision']


def test_promote_reinforces_existing_similar_standard(app, db_session):
    from db import SubjectStandard, Teacher, Assignment, Submission, FeedbackEdit
    from subject_standards import promote_to_subject_standard
    from unittest.mock import patch

    t = Teacher(id='t-2', name='Joe', access_code='J2', role='teacher')
    db_session.add(t)
    asn = Assignment(id='a-2', classroom_code='XYZ', subject='biology', title='Bio2')
    db_session.add(asn)
    db_session.commit()
    sub = Submission(assignment_id=asn.id, student_id=None, result_json='{}')
    db_session.add(sub)
    pre = SubjectStandard(
        subject='biology',
        text="Must say 'temperature', not 'heat'.",
        topic_keys='["enzymes", "terminology_precision"]',
        theme_key='terminology_precision',
        status='active', created_by=t.id, reinforcement_count=3,
    )
    db_session.add(pre)
    db_session.commit()
    pre_id = pre.id

    fe = FeedbackEdit(
        submission_id=sub.id, criterion_id='1', field='feedback',
        original_text='Heat is fine.',
        edited_text="Must say temperature instead of heat.",
        edited_by=t.id, theme_key='terminology_precision',
        assignment_id=asn.id, rubric_version='v1', scope='promoted', active=True,
    )
    db_session.add(fe)
    db_session.commit()

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes', 'terminology_precision']):
        ss_id = promote_to_subject_standard(
            feedback_edit_id=fe.id,
            provider='anthropic', model='claude-haiku-4-5',
            session_keys={'anthropic': 'sk-fake'},
        )

    assert ss_id == pre_id
    assert SubjectStandard.query.filter_by(subject='biology').count() == 1
    db_session.refresh(pre)
    assert pre.reinforcement_count == 4
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Append to `subject_standards.py`**

```python
def _text_similarity(a: str, b: str) -> float:
    """Cheap similarity score in [0, 1]. SequenceMatcher is enough at
    our scale — we're dedup'ing principles of ~50–250 chars, not search."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def find_similar_standard(subject: str, topic_keys: list, text: str):
    """Return an existing SubjectStandard with same subject, overlapping topic_keys,
    and text similarity >= SIMILARITY_THRESHOLD. Otherwise None."""
    if not topic_keys:
        return None
    candidates = SubjectStandard.query.filter_by(subject=subject).all()
    for c in candidates:
        c_keys = json.loads(c.topic_keys or '[]')
        if not any(k in c_keys for k in topic_keys):
            continue
        if _text_similarity(c.text, text) >= SIMILARITY_THRESHOLD:
            return c
    return None


def promote_to_subject_standard(feedback_edit_id, provider, model, session_keys):
    """Promote a FeedbackEdit to a SubjectStandard. Reinforces an existing
    near-duplicate or inserts a new pending_review row. Returns the
    SubjectStandard id."""
    from ai_marking import extract_standard_topic_keys

    fe = FeedbackEdit.query.get(feedback_edit_id)
    if fe is None:
        raise ValueError(f'feedback_edit {feedback_edit_id} not found')

    from db import Assignment, Submission
    asn = Assignment.query.get(fe.assignment_id)
    subject = (asn.subject or '').strip().lower()

    question_text = ''
    if fe.submission_id and fe.criterion_id:
        sub = Submission.query.get(fe.submission_id)
        if sub:
            result = sub.get_result() or {}
            for q in (result.get('questions') or []):
                if str(q.get('question_num')) == str(fe.criterion_id):
                    question_text = q.get('question', '') or ''
                    break

    topic_keys = extract_standard_topic_keys(
        provider=provider, model=model, session_keys=session_keys,
        subject=subject,
        question_text=question_text,
        original_feedback=fe.original_text or '',
        edited_feedback=fe.edited_text or '',
        theme_key=fe.theme_key,
    )

    existing = find_similar_standard(subject, topic_keys, fe.edited_text or '')
    if existing is not None:
        existing.reinforcement_count = (existing.reinforcement_count or 0) + 1
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.updated_at = datetime.now(timezone.utc)
        sources = json.loads(existing.source_feedback_edit_ids or '[]')
        if fe.id not in sources:
            sources.append(fe.id)
        existing.source_feedback_edit_ids = json.dumps(sources)
        fe.promoted_to_subject_standard_id = existing.id
        db.session.commit()
        return existing.id

    ss = SubjectStandard(
        subject=subject,
        text=fe.edited_text or '',
        topic_keys=json.dumps(topic_keys),
        theme_key=fe.theme_key,
        status='pending_review',
        created_by=fe.edited_by,
        source_feedback_edit_ids=json.dumps([fe.id]),
        reinforcement_count=1,
    )
    db.session.add(ss)
    db.session.flush()
    fe.promoted_to_subject_standard_id = ss.id
    db.session.commit()
    return ss.id
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add subject_standards.py tests/test_subject_standards.py
git commit -m "feat(standards): promote_to_subject_standard with dedup + reinforcement"
```

### Task 4.2: `retrieve_subject_standards` for marking-time injection

- [ ] **Step 1: Add failing tests**

Append:

```python
def test_retrieve_subject_standards_returns_topic_matched_active(app, db_session):
    from db import SubjectStandard
    from subject_standards import retrieve_subject_standards

    db_session.add_all([
        SubjectStandard(subject='biology', text='A', topic_keys='["enzymes"]',
                        status='active', created_by='t-1', reinforcement_count=5),
        SubjectStandard(subject='biology', text='B', topic_keys='["genetics"]',
                        status='active', created_by='t-1', reinforcement_count=10),
        SubjectStandard(subject='biology', text='C', topic_keys='["enzymes"]',
                        status='pending_review', created_by='t-1', reinforcement_count=20),
    ])
    db_session.commit()

    out = retrieve_subject_standards(
        subject='biology',
        per_question_topic_keys=[['enzymes', 'terminology_precision']],
    )
    texts = [s.text for s in out]
    assert 'A' in texts
    assert 'B' not in texts
    assert 'C' not in texts


def test_retrieve_subject_standards_respects_per_topic_quota_and_cap(app, db_session):
    from db import SubjectStandard
    from subject_standards import retrieve_subject_standards

    for i in range(5):
        db_session.add(SubjectStandard(
            subject='biology', text=f'enzymes-{i}',
            topic_keys='["enzymes"]', status='active',
            created_by='t-1', reinforcement_count=i,
        ))
    db_session.commit()

    out = retrieve_subject_standards(
        subject='biology',
        per_question_topic_keys=[['enzymes']],
    )
    assert len(out) == 3
    assert out[0].text == 'enzymes-4'
    assert out[2].text == 'enzymes-2'


def test_retrieve_returns_empty_when_no_topics(app, db_session):
    from subject_standards import retrieve_subject_standards
    out = retrieve_subject_standards(subject='biology', per_question_topic_keys=[[]])
    assert out == []
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Append to `subject_standards.py`**

```python
def retrieve_subject_standards(subject: str, per_question_topic_keys: list):
    """Pull active SubjectStandards for the assignment.

    Algorithm (see §4.5 of the spec):
      1. For each question's topic_keys, query active standards whose
         topic_keys overlap.
      2. Per matched topic, take top `PER_TOPIC_QUOTA` by reinforcement_count.
      3. Dedup across questions/topics.
      4. Apply hard `ABSOLUTE_CAP`.

    Returns ordered list of SubjectStandard rows.
    """
    if not per_question_topic_keys:
        return []
    all_topic_keys = set()
    for tk_list in per_question_topic_keys:
        for k in (tk_list or []):
            all_topic_keys.add(k)
    if not all_topic_keys:
        return []

    candidates = (
        SubjectStandard.query
        .filter_by(subject=subject, status='active')
        .all()
    )

    seen_ids = set()
    selected = []
    for topic in all_topic_keys:
        topic_candidates = []
        for c in candidates:
            if c.id in seen_ids:
                continue
            ck = json.loads(c.topic_keys or '[]')
            if topic in ck:
                topic_candidates.append(c)
        topic_candidates.sort(key=lambda r: (-(r.reinforcement_count or 0), -(r.id or 0)))
        for c in topic_candidates[:PER_TOPIC_QUOTA]:
            seen_ids.add(c.id)
            selected.append(c)

    remaining = []
    for c in candidates:
        if c.id in seen_ids:
            continue
        ck = json.loads(c.topic_keys or '[]')
        if any(k in all_topic_keys for k in ck):
            remaining.append(c)
    remaining.sort(key=lambda r: (-(r.reinforcement_count or 0), -(r.id or 0)))
    for c in remaining:
        if len(selected) >= ABSOLUTE_CAP:
            break
        selected.append(c)

    return selected[:ABSOLUTE_CAP]
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add subject_standards.py tests/test_subject_standards.py
git commit -m "feat(standards): retrieve_subject_standards with per-topic quota + 30 cap"
```

---

## Phase 5: Backend save endpoint — two-checkbox routing

### Task 5.1: Update `_process_text_edit` to accept new intent flags

**Files:**
- Modify: `app.py` — function `_process_text_edit` (line 7147)
- Modify: `app.py` — function `teacher_submission_result_patch` (line 7246)
- Test: `tests/test_calibration_intent.py`

- [ ] **Step 1: Create failing test**

Create `tests/test_calibration_intent.py`:

```python
"""UP-: two-checkbox intent (Amend answer key / Update subject standards)."""

import json
from unittest.mock import patch

from db import db, Teacher, Assignment, Submission, FeedbackEdit


def _make_teacher_and_assignment(db_session, subject='biology'):
    t = Teacher(id='t-1', name='Joe', access_code='JOE1', role='owner')
    db_session.add(t)
    asn = Assignment(id='a-1', classroom_code='ABC123', subject=subject, title='Test', teacher_id=t.id,
                     topic_keys=json.dumps([['enzymes']]), topic_keys_status='tagged',
                     provider='anthropic', model='claude-sonnet-4-6')
    db_session.add(asn)
    sub = Submission(assignment_id=asn.id, student_id=None, result_json=json.dumps({
        'questions': [
            {'question_num': 1, 'feedback': 'Correct.', 'theme_key': 'terminology_precision'},
        ],
    }), provider='anthropic', model='claude-sonnet-4-6')
    db_session.add(sub)
    db_session.commit()
    return t, asn, sub


def test_neither_box_ticked_writes_no_feedback_edit(app, db_session, client):
    t, asn, sub = _make_teacher_and_assignment(db_session)
    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv = client.patch(
        f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
        json={'questions': [{'question_num': 1, 'feedback': 'New text',
                              'amend_answer_key': False, 'update_subject_standards': False}]},
    )
    assert rv.status_code == 200
    assert FeedbackEdit.query.filter_by(submission_id=sub.id).count() == 0


def test_amend_answer_key_only_writes_feedback_edit_with_flag(app, db_session, client):
    t, asn, sub = _make_teacher_and_assignment(db_session)
    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv = client.patch(
        f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
        json={'questions': [{'question_num': 1, 'feedback': 'Accept "powerhouse of the cell"',
                              'amend_answer_key': True, 'update_subject_standards': False}]},
    )
    assert rv.status_code == 200
    fe = FeedbackEdit.query.filter_by(submission_id=sub.id).first()
    assert fe is not None
    assert fe.amend_answer_key is True
    assert fe.scope == 'amendment'
    assert fe.promoted_to_subject_standard_id is None


def test_update_subject_standards_only_triggers_promotion(app, db_session, client):
    t, asn, sub = _make_teacher_and_assignment(db_session)
    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes', 'terminology_precision']):
        rv = client.patch(
            f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
            json={'questions': [{'question_num': 1, 'feedback': "Must say 'temperature'",
                                  'amend_answer_key': False, 'update_subject_standards': True}]},
        )
    assert rv.status_code == 200
    fe = FeedbackEdit.query.filter_by(submission_id=sub.id).first()
    assert fe.amend_answer_key is False
    assert fe.scope == 'promoted'
    assert fe.promoted_to_subject_standard_id is not None
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Modify `_process_text_edit` signature and body in `app.py`**

Replace `_process_text_edit` (line 7147–7242) with:

```python
def _process_text_edit(submission, criterion_id, field, edited_text,
                      teacher_id, assignment,
                      amend_answer_key, update_subject_standards,
                      current_text,
                      provider=None, ai_model=None, session_keys=None):
    """Log a teacher edit to feedback_log; if amend_answer_key or
    update_subject_standards, also write a FeedbackEdit row with the
    appropriate flags + scope. If update_subject_standards, promote.

    Returns {'version': N, 'amend_answer_key': bool, 'update_subject_standards': bool,
            'promoted_standard_id': int | None} on a real change, or None
    when edited_text equals current_text.
    """
    from db import FeedbackLog, FeedbackEdit
    from sqlalchemy import func as _func

    if (edited_text or '') == (current_text or ''):
        return None

    max_v = db.session.query(_func.max(FeedbackLog.version)).filter(
        FeedbackLog.submission_id == submission.id,
        FeedbackLog.criterion_id == criterion_id,
        FeedbackLog.field == field,
    ).scalar() or 0
    new_version = max_v + 1

    db.session.add(FeedbackLog(
        submission_id=submission.id,
        criterion_id=criterion_id,
        field=field,
        version=new_version,
        feedback_text=edited_text or '',
        author_type='teacher',
        author_id=teacher_id,
    ))

    promoted_standard_id = None
    write_edit_row = bool(amend_answer_key or update_subject_standards)

    if write_edit_row:
        v1 = FeedbackLog.query.filter_by(
            submission_id=submission.id,
            criterion_id=criterion_id,
            field=field,
            version=1,
        ).first()
        if not v1:
            v1 = FeedbackLog(
                submission_id=submission.id,
                criterion_id=criterion_id,
                field=field,
                version=1,
                feedback_text=current_text or '',
                author_type='ai',
                author_id=None,
            )
            db.session.add(v1)
            db.session.flush()
        original_text = v1.feedback_text or (current_text or '')

        FeedbackEdit.query.filter_by(
            edited_by=teacher_id,
            assignment_id=assignment.id,
            criterion_id=criterion_id,
            field=field,
            active=True,
        ).update({'active': False})

        theme_key = None
        result_for_theme = submission.get_result() or {}
        for q in (result_for_theme.get('questions') or []):
            if str(q.get('question_num')) == criterion_id:
                theme_key = q.get('theme_key')
                break

        from ai_marking import _rubric_version_hash
        scope = 'promoted' if update_subject_standards else 'amendment'
        new_fe = FeedbackEdit(
            submission_id=submission.id,
            criterion_id=criterion_id,
            field=field,
            original_text=original_text,
            edited_text=edited_text or '',
            edited_by=teacher_id,
            theme_key=theme_key,
            assignment_id=assignment.id,
            rubric_version=_rubric_version_hash(assignment),
            scope=scope,
            amend_answer_key=bool(amend_answer_key),
            active=True,
        )
        db.session.add(new_fe)
        db.session.flush()

        if update_subject_standards and provider and ai_model and session_keys:
            try:
                from subject_standards import promote_to_subject_standard
                promoted_standard_id = promote_to_subject_standard(
                    feedback_edit_id=new_fe.id,
                    provider=provider, model=ai_model, session_keys=session_keys,
                )
            except Exception as e:
                logger.warning(f'promote_to_subject_standard failed for edit {new_fe.id}: {e}')

    return {
        'version': new_version,
        'amend_answer_key': bool(amend_answer_key),
        'update_subject_standards': bool(update_subject_standards),
        'promoted_standard_id': promoted_standard_id,
    }
```

- [ ] **Step 4: Update `teacher_submission_result_patch` callers**

In `app.py`, find every call site of `_process_text_edit` inside `teacher_submission_result_patch` (around lines 7430–7520). Replace each `calibrate=...` argument with:

```python
amend_answer_key=q.get('amend_answer_key', False),
update_subject_standards=q.get('update_subject_standards', False),
provider=asn.provider,
ai_model=asn.model,
session_keys=asn.get_api_keys() or {},
```

- [ ] **Step 5: Update the response `edit_meta` shape**

Wherever the old code wrote `'calibrated': True` into `edit_meta`, replace with:

```python
edit_meta.setdefault(str(qn), {})[_field] = {
    'version': r['version'],
    'amend_answer_key': r['amend_answer_key'],
    'update_subject_standards': r['update_subject_standards'],
    'promoted_standard_id': r.get('promoted_standard_id'),
}
```

- [ ] **Step 6: Run — expect PASS (3 new tests)**

```bash
pytest tests/test_calibration_intent.py -v
```

- [ ] **Step 7: Run full suite to check for regressions**

```bash
pytest tests/ -v
```

If any existing test referenced `calibrate=` in a stub, update it to the new flag names.

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_calibration_intent.py
git commit -m "feat(api): two-checkbox intent routing on feedback edit save"
```

---

## Phase 6: Frontend — replace single checkbox with two checkboxes

### Task 6.1: Update `static/js/feedback_render.js` UI + payload

- [ ] **Step 1: Locate the existing checkbox**

```bash
grep -n "Save to calibration bank\|calibrate" /Users/changshien/Documents/Github/ai-marking-prototype/static/js/feedback_render.js | head -10
```

Confirm: single checkbox rendered around line 1790–1824; payload assembled around line 1982–2071.

- [ ] **Step 2: Replace the single-checkbox markup**

Find the HTML string rendering `Save to calibration bank` and replace with:

```javascript
var subjectStandardsEnabled = (
    window.ASSIGNMENT_HAS_CANONICAL_SUBJECT === true &&
    window.ASSIGNMENT_TOPIC_KEYS_STATUS !== 'legacy'
);
var intentHtml =
    '<label class="fv-intent-row">' +
    '  <input type="checkbox" class="fv-amend-answer-key" />' +
    '  Amend answer key for this assignment' +
    '</label>' +
    (subjectStandardsEnabled
        ? '<label class="fv-intent-row">' +
          '  <input type="checkbox" class="fv-update-subject-standards" />' +
          '  Update subject standards' +
          '</label>'
        : '');
```

Render `intentHtml` in place of the old checkbox markup.

- [ ] **Step 3: Update the PATCH payload assembly**

Find the existing `body: JSON.stringify({...})` call. Replace the per-question payload entry to include:

```javascript
var amendBox = card.querySelector('.fv-amend-answer-key');
var promoteBox = card.querySelector('.fv-update-subject-standards');
var amend_answer_key = !!(amendBox && amendBox.checked);
var update_subject_standards = !!(promoteBox && promoteBox.checked);

// In each per-question payload entry:
{
    question_num: qnum,
    feedback: text,
    amend_answer_key: amend_answer_key,
    update_subject_standards: update_subject_standards,
}
```

Remove any remaining `calibrate: true/false` from the same payload entry.

- [ ] **Step 4: Update the response handler**

Find consumption of `response.edit_meta[...].calibrated`. Replace with:

```javascript
var meta = (response.edit_meta || {})[qnum] || {};
var fieldMeta = meta[field] || {};
if (fieldMeta.amend_answer_key) {
    // show "Amended" indicator
}
if (fieldMeta.update_subject_standards) {
    // show "Promoted" indicator
}
```

Match the existing visual indicator style — the goal is post-save UI continuity for teachers who only used the old single checkbox.

- [ ] **Step 5: Manually verify in browser**

```bash
python app.py
```

Open a marked submission, edit a feedback field, tick "Amend answer key", save. Verify the network POST to `/teacher/.../result` contains `amend_answer_key: true` and no `calibrate` field. Verify DB:

```bash
sqlite3 marking.db "SELECT amend_answer_key, scope FROM feedback_edit ORDER BY id DESC LIMIT 1"
```

Expected: `1|amendment`.

- [ ] **Step 6: Commit**

```bash
git add static/js/feedback_render.js
git commit -m "feat(ui): two intent checkboxes replace single calibration toggle"
```

### Task 6.2: Inject `ASSIGNMENT_HAS_CANONICAL_SUBJECT` and topic_keys_status into template

- [ ] **Step 1: Find the right template**

```bash
grep -rn "feedback_render.js" /Users/changshien/Documents/Github/ai-marking-prototype/templates/
```

- [ ] **Step 2: Add JS globals**

In the template's `<script>` block (or a new one before the feedback_render.js include):

```jinja
<script>
window.ASSIGNMENT_HAS_CANONICAL_SUBJECT = {{ (assignment_has_canonical_subject|default(false))|tojson }};
window.ASSIGNMENT_TOPIC_KEYS_STATUS = {{ (assignment.topic_keys_status|default('pending'))|tojson }};
</script>
```

The route rendering this page must compute `assignment_has_canonical_subject = subjects.resolve_subject_key(asn.subject) is not None`. Add to the route's `render_template` call.

- [ ] **Step 3: Commit**

```bash
git add templates/<file>.html app.py
git commit -m "feat(ui): expose canonical-subject + topic_keys_status to feedback_render.js"
```

---

## Phase 7: Effective answer key — assembly and preview

### Task 7.1: Compute the merged answer key

- [ ] **Step 1: Add failing test**

Append to `tests/test_calibration_intent.py`:

```python
def test_effective_answer_key_appends_amendments(app, db_session):
    from subject_standards import build_effective_answer_key
    from db import FeedbackEdit, Teacher, Assignment

    t = Teacher(id='t-2', name='Joe', access_code='JOE2', role='owner')
    db_session.add(t)
    asn = Assignment(id='a-2', classroom_code='ZZZ', subject='biology', title='Bio')
    db_session.add(asn)
    db_session.commit()

    db_session.add_all([
        FeedbackEdit(
            submission_id=1, criterion_id='3', field='feedback',
            original_text='X', edited_text='Accept "powerhouse of the cell"',
            edited_by=t.id, assignment_id=asn.id, rubric_version='v1',
            scope='amendment', amend_answer_key=True, active=True,
        ),
        FeedbackEdit(
            submission_id=1, criterion_id='5', field='feedback',
            original_text='X', edited_text='Diagram is a fish, not a bird',
            edited_by=t.id, assignment_id=asn.id, rubric_version='v1',
            scope='amendment', amend_answer_key=True, active=True,
        ),
    ])
    db_session.commit()

    merged = build_effective_answer_key(
        assignment=asn,
        original_answer_key_text='Q1: mitochondria\nQ2: ATP',
    )
    assert 'Teacher clarifications' in merged
    assert 'Q3' in merged
    assert 'Q5' in merged
    assert 'powerhouse' in merged
    assert 'mitochondria' in merged


def test_effective_answer_key_no_amendments_returns_original(app, db_session):
    from subject_standards import build_effective_answer_key
    from db import Assignment
    asn = Assignment(id='a-3', classroom_code='ZZZ3', subject='biology', title='Bio')
    db_session.add(asn)
    db_session.commit()
    merged = build_effective_answer_key(assignment=asn, original_answer_key_text='Q1: x')
    assert merged.strip() == 'Q1: x'
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Append to `subject_standards.py`**

The `_rubric_version_hash` import is inside the function to avoid circular import.

```python
def build_effective_answer_key(assignment, original_answer_key_text: str) -> str:
    """Return the original answer key text concatenated with a 'Teacher
    clarifications' section assembled from active amend_answer_key edits
    scoped to this assignment + rubric_version."""
    from ai_marking import _rubric_version_hash
    from db import Teacher

    rv = _rubric_version_hash(assignment)
    edits = (
        FeedbackEdit.query
        .filter_by(
            assignment_id=assignment.id,
            rubric_version=rv,
            active=True,
            amend_answer_key=True,
        )
        .order_by(FeedbackEdit.created_at.desc())
        .all()
    )
    if not edits:
        return original_answer_key_text or ''

    lines = [
        '',
        '── Teacher clarifications (added since upload) ──',
        '',
    ]
    for fe in edits:
        teacher = Teacher.query.get(fe.edited_by)
        name = teacher.name if teacher else 'teacher'
        date = fe.created_at.strftime('%Y-%m-%d') if fe.created_at else ''
        qn = fe.criterion_id
        lines.append(f"Q{qn}: {fe.edited_text}")
        lines.append(f"    Added by {name}, {date}.")
        lines.append('')

    return (original_answer_key_text or '') + '\n' + '\n'.join(lines)
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add subject_standards.py tests/test_calibration_intent.py
git commit -m "feat(answer-key): build_effective_answer_key merges amendments"
```

---

## Phase 8: Marking-time prompt injection

### Task 8.1: Inject effective answer key + subject standards into the marking prompt

- [ ] **Step 1: Replace `build_calibration_block` with empty-string stub**

In `ai_marking.py`, replace the body of `build_calibration_block` (line 2797–2826) with:

```python
def build_calibration_block(teacher_id, asn, subject, theme_keys,
                            provider=None, model=None, session_keys=None):
    """[Deprecated] Returns an empty string. Subject-standard retrieval has
    moved to subject_standards.retrieve_subject_standards and is injected
    separately by the marking prompt builder."""
    return ''
```

This isolates the rewrite — old callers still work but inject nothing.

- [ ] **Step 2: Extend `_build_rubrics_prompt` and `_build_short_answer_prompt`**

In each of these (lines 1103 and 1280), add two new kwargs:

```python
def _build_rubrics_prompt(subject, rubrics_pages, reference_pages, question_paper_pages,
                          ...existing kwargs...,
                          amendments_text='', subject_standards_block=''):
    ...existing body...
    if amendments_text:
        content.append({'type': 'text', 'text': amendments_text})
    if subject_standards_block:
        content.append({'type': 'text', 'text': subject_standards_block})
```

Match the exact content-block style used by surrounding code. Do the same for `_build_short_answer_prompt`.

- [ ] **Step 3: Replace the old calibration plumb at the `mark_script` call site**

In `mark_script` (line 1538), find where `build_calibration_block` is called. Replace with:

```python
# Build amendments + subject standards
from subject_standards import build_effective_answer_key, retrieve_subject_standards
import json as _json

amendments_text = ''
subject_standards_block = ''
if assignment is not None:
    # Amendments
    original_ak_text = getattr(assignment, '_original_ak_text', None) or ''
    full_ak_text = build_effective_answer_key(assignment, original_ak_text)
    marker = '── Teacher clarifications'
    if marker in full_ak_text:
        idx = full_ak_text.find(marker)
        amendments_text = full_ak_text[idx:]

    # Subject standards
    if assignment.subject:
        per_q_topic_keys = _json.loads(assignment.topic_keys or '[]')
        subj_key = (assignment.subject or '').strip().lower()
        standards = retrieve_subject_standards(
            subject=subj_key,
            per_question_topic_keys=per_q_topic_keys,
        )
        if standards:
            lines = ['── Subject standards relevant to this assignment ──', '']
            for s in standards:
                tk = _json.loads(s.topic_keys or '[]')
                tk_label = ', '.join(tk) or '(general)'
                lines.append(f'For {tk_label}:')
                lines.append(f'  - {s.text}')
                lines.append('')
            subject_standards_block = '\n'.join(lines)
```

Then pass `amendments_text` and `subject_standards_block` into the prompt builder call.

- [ ] **Step 4: Add an integration test**

Append to `tests/test_subject_standards.py`:

```python
def test_retrieval_pulls_active_standards_for_matching_topics(app, db_session):
    from db import SubjectStandard
    from subject_standards import retrieve_subject_standards

    db_session.add(SubjectStandard(
        subject='biology', text="Accept 'temperature', reject 'heat'.",
        topic_keys='["enzymes"]', status='active', created_by='t-1',
        reinforcement_count=5,
    ))
    db_session.commit()

    out = retrieve_subject_standards(
        subject='biology',
        per_question_topic_keys=[['enzymes', 'terminology_precision'], ['cellular_respiration']],
    )
    assert any("temperature" in s.text for s in out)
```

- [ ] **Step 5: Run — expect PASS**

```bash
pytest tests/test_subject_standards.py -v
```

- [ ] **Step 6: Commit**

```bash
git add ai_marking.py tests/test_subject_standards.py
git commit -m "feat(marking): inject amendments + subject standards into marking prompt"
```

---

## Phase 9: Assignment-page UI — topic tags + amendments preview + bank push

### Task 9.1: Topic tags section on assignment page

- [ ] **Step 1: Locate the assignment detail template**

```bash
grep -rn "answer_key\|question_paper\|/teacher/assignment/" /Users/changshien/Documents/Github/ai-marking-prototype/templates/ | head -20
```

Identify the section displaying assignment metadata.

- [ ] **Step 2: Add the section**

Insert into the assignment-detail template:

```jinja
{% if assignment.topic_keys_status != 'legacy' %}
  <div class="card topic-tags-card">
    <h4>Topic tags
      {% if assignment.topic_keys_status == 'pending' %}
        <span class="badge badge-warning">tagging…</span>
      {% endif %}
    </h4>
    {% set tk_per_q = assignment.topic_keys | safe_json_loads(default=[]) %}
    {% if not tk_per_q %}
      <p class="muted">Topic tagging hasn't run yet. Marking will still work — subject standards won't apply until tags are present.</p>
      <button class="btn-link" data-action="run-topic-tagging">Run tagging now</button>
    {% else %}
      <ol class="topic-tag-list">
        {% for q_keys in tk_per_q %}
          <li>
            Q{{ loop.index }}:
            <input class="topic-tag-input" data-q="{{ loop.index }}"
                   value="{{ q_keys | join(', ') }}"
                   placeholder="comma-separated topic keys" />
          </li>
        {% endfor %}
      </ol>
    {% endif %}
  </div>
{% endif %}
```

- [ ] **Step 3: Register `safe_json_loads` Jinja filter**

In `app.py`:

```python
@app.template_filter('safe_json_loads')
def _safe_json_loads(s, default=None):
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else []
```

- [ ] **Step 4: Add save endpoint**

```python
@app.route('/teacher/assignment/<assignment_id>/topic-tags', methods=['PUT'])
def teacher_assignment_topic_tags_update(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    raw = data.get('topic_keys')
    if not isinstance(raw, list):
        return jsonify({'success': False, 'error': 'topic_keys must be a list'}), 400

    from config.subject_topics import is_known_topic_key
    subj_key = (asn.subject or '').strip().lower()
    cleaned = []
    for q_keys in raw:
        if not isinstance(q_keys, list):
            cleaned.append([])
            continue
        kept = [k for k in q_keys if isinstance(k, str) and is_known_topic_key(subj_key, k)]
        cleaned.append(kept[:3])

    asn.topic_keys = json.dumps(cleaned)
    asn.topic_keys_status = 'tagged'
    db.session.commit()
    return jsonify({'success': True, 'topic_keys': cleaned})
```

- [ ] **Step 5: Wire frontend autosave on blur**

In a `<script>` block on the assignment template:

```javascript
document.querySelectorAll('.topic-tag-input').forEach(function (input) {
    input.addEventListener('blur', function () {
        var inputs = document.querySelectorAll('.topic-tag-input');
        var payload = [];
        inputs.forEach(function (el) {
            payload.push(
                el.value.split(',')
                    .map(function (s) { return s.trim(); })
                    .filter(Boolean)
            );
        });
        fetch('{{ url_for("teacher_assignment_topic_tags_update", assignment_id=assignment.id) }}', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic_keys: payload }),
        });
    });
});
```

- [ ] **Step 6: Manual verification**

Start server, open an assignment, edit a topic input, blur. Verify PUT fires and DB updates.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/<file>.html
git commit -m "feat(ui): topic tags section + inline edit on assignment page"
```

### Task 9.2: Effective answer key preview

- [ ] **Step 1: Add preview route**

In `app.py`:

```python
@app.route('/teacher/assignment/<assignment_id>/answer-key-preview')
def teacher_assignment_answer_key_preview(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    original_ak_text = ''
    try:
        if asn.answer_key:
            from ai_marking import _decode_answer_key_text
            original_ak_text = _decode_answer_key_text(asn.answer_key) or ''
    except Exception as e:
        logger.warning(f'answer-key preview: decode failed: {e}')
    from subject_standards import build_effective_answer_key
    merged = build_effective_answer_key(asn, original_ak_text)
    return jsonify({'success': True, 'effective_answer_key': merged})
```

If `_decode_answer_key_text` doesn't yet exist in `ai_marking.py`, add a minimal helper:

```python
def _decode_answer_key_text(blob: bytes) -> str:
    """Decode answer_key blob to text. PDF → pdf2image → OCR if possible;
    otherwise UTF-8 best-effort. Returns '' on failure."""
    if not blob:
        return ''
    try:
        return blob.decode('utf-8')
    except UnicodeDecodeError:
        pass
    try:
        from pdf2image import convert_from_bytes
        pages = convert_from_bytes(blob, dpi=150)
        # Without OCR, fall back to empty — text extraction from images is
        # outside the scope of this preview. Marking-time uses the PDF directly.
        return ''
    except Exception:
        return ''
```

- [ ] **Step 2: Add button + display in template**

```jinja
<button class="btn-secondary" data-action="preview-effective-answer-key">
  Preview effective answer key
</button>
<pre id="effective-ak-output" hidden style="white-space:pre-wrap"></pre>
```

```javascript
document.querySelector('[data-action="preview-effective-answer-key"]')
    .addEventListener('click', function () {
        fetch('{{ url_for("teacher_assignment_answer_key_preview", assignment_id=assignment.id) }}')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var pre = document.getElementById('effective-ak-output');
                pre.textContent = data.effective_answer_key || '(empty)';
                pre.hidden = false;
            });
    });
```

- [ ] **Step 3: Manual verification**

Open an assignment, add an amendment, click Preview. Verify the clarification appears appended.

- [ ] **Step 4: Commit**

```bash
git add app.py templates/<file>.html ai_marking.py
git commit -m "feat(ui): preview effective answer key with teacher clarifications"
```

### Task 9.3: Bank push button + optimistic concurrency

- [ ] **Step 1: Write failing test**

Create `tests/test_bank_amendment_push.py`:

```python
"""UP-: bank amendment push with optimistic concurrency."""

import json
from datetime import datetime, timezone
from db import db, Teacher, Assignment, AssignmentBank, FeedbackEdit


def _setup(db_session):
    t = Teacher(id='t-9', name='Joe', access_code='JOE9', role='owner')
    db_session.add(t)
    bank = AssignmentBank(id='b-1', title='Bio', subject='biology')
    db_session.add(bank)
    asn = Assignment(id='a-9', classroom_code='WXY', subject='biology',
                     title='Bio', teacher_id=t.id)
    db_session.add(asn)
    db_session.commit()
    return t, bank, asn


def test_push_overwrites_bank_with_amendments(app, db_session, client):
    t, bank, asn = _setup(db_session)
    db_session.add(FeedbackEdit(
        submission_id=1, criterion_id='1', field='feedback',
        original_text='X', edited_text='Accept powerhouse',
        edited_by=t.id, assignment_id=asn.id, rubric_version='v1',
        scope='amendment', amend_answer_key=True, active=True,
    ))
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv = client.post(
        f'/teacher/assignment/{asn.id}/push-amendments-to-bank',
        json={'bank_id': bank.id, 'last_known_bank_pushed_at': None},
    )
    assert rv.status_code == 200
    db_session.refresh(asn)
    assert asn.bank_pushed_at is not None


def test_push_returns_409_on_concurrent_write(app, db_session, client):
    t, bank, asn = _setup(db_session)
    asn.bank_pushed_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv = client.post(
        f'/teacher/assignment/{asn.id}/push-amendments-to-bank',
        json={
            'bank_id': bank.id,
            'last_known_bank_pushed_at': '2026-04-15T00:00:00Z',
        },
    )
    assert rv.status_code == 409
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add the route**

```python
@app.route('/teacher/assignment/<assignment_id>/push-amendments-to-bank', methods=['POST'])
def teacher_push_amendments_to_bank(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    bank_id = data.get('bank_id')
    if not bank_id:
        return jsonify({'success': False, 'error': 'bank_id required'}), 400
    bank = AssignmentBank.query.get(bank_id)
    if not bank:
        return jsonify({'success': False, 'error': 'bank not found'}), 404

    client_ts_raw = data.get('last_known_bank_pushed_at')
    client_ts = None
    if client_ts_raw:
        try:
            client_ts = datetime.fromisoformat(client_ts_raw.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'success': False, 'error': 'invalid timestamp'}), 400
    server_ts = asn.bank_pushed_at
    if server_ts and client_ts and server_ts > client_ts:
        return jsonify({
            'success': False,
            'error': 'concurrent_write',
            'server_bank_pushed_at': server_ts.isoformat(),
        }), 409
    if server_ts and not client_ts:
        return jsonify({
            'success': False,
            'error': 'concurrent_write_no_client_ts',
            'server_bank_pushed_at': server_ts.isoformat(),
        }), 409

    from subject_standards import build_effective_answer_key
    from ai_marking import _decode_answer_key_text
    original_ak = _decode_answer_key_text(asn.answer_key) if asn.answer_key else ''
    merged = build_effective_answer_key(asn, original_ak)
    bank.answer_key = merged.encode('utf-8') if isinstance(merged, str) else merged

    now = datetime.now(timezone.utc)
    asn.bank_pushed_at = now
    db.session.commit()
    return jsonify({'success': True, 'bank_pushed_at': now.isoformat()})
```

- [ ] **Step 4: Add button + modal in template**

```jinja
{% if assignment.id in bank_linked_assignment_ids %}
<button class="btn-primary" data-action="push-amendments-to-bank">
  Update bank version with my amendments
</button>
<div id="push-modal" hidden>
  <p>Bank version was last updated <span data-bank-last-updated></span>.</p>
  <button data-action="confirm-push">Push to bank</button>
  <button data-action="cancel-push">Cancel</button>
</div>
{% endif %}
```

Bind JS:

```javascript
document.querySelector('[data-action="push-amendments-to-bank"]').addEventListener('click', function () {
    document.getElementById('push-modal').hidden = false;
});
document.querySelector('[data-action="confirm-push"]').addEventListener('click', function () {
    fetch('{{ url_for("teacher_push_amendments_to_bank", assignment_id=assignment.id) }}', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            bank_id: '{{ bank_id }}',
            last_known_bank_pushed_at: '{{ assignment.bank_pushed_at.isoformat() if assignment.bank_pushed_at else "" }}' || null,
        }),
    }).then(function (r) {
        if (r.status === 409) {
            return r.json().then(function (d) {
                alert('Bank was updated by someone else after you pulled. Push again to overwrite.');
            });
        }
        if (r.ok) location.reload();
    });
});
```

(Compute `bank_linked_assignment_ids` and `bank_id` in the route that renders this page.)

- [ ] **Step 5: Run — expect PASS**

```bash
pytest tests/test_bank_amendment_push.py -v
```

- [ ] **Step 6: Commit**

```bash
git add app.py templates/<file>.html tests/test_bank_amendment_push.py
git commit -m "feat(bank): push-amendments-to-bank with optimistic concurrency"
```

---

## Phase 10: Subject standards page

### Task 10.1: ACL helper + route + template skeleton

- [ ] **Step 1: Add failing permission tests**

Append to `tests/test_subject_standards.py`:

```python
def test_subject_standards_page_requires_hod_or_subject_lead(app, db_session, client):
    from db import Teacher
    t = Teacher(id='t-non-lead', name='Bob', access_code='BOB1', role='teacher')
    db_session.add(t)
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv = client.get('/teacher/subject-standards')
    assert rv.status_code == 403


def test_subject_standards_page_accessible_by_hod(app, db_session, client):
    from db import Teacher
    t = Teacher(id='t-hod', name='HOD', access_code='HOD1', role='hod')
    db_session.add(t)
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv = client.get('/teacher/subject-standards')
    assert rv.status_code == 200
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add ACL helper and route in `app.py`**

```python
def _is_dept_mode():
    """Check whether DEPT_MODE env var is TRUE. Mirror existing pattern."""
    return os.environ.get('DEPT_MODE', 'FALSE').upper() == 'TRUE'


def _can_edit_subject_standards(teacher, subject=None):
    """HOD: yes for any subject. Subject lead: yes for that subject.
    Non-dept mode: yes for the sole teacher."""
    if teacher is None:
        return False
    if (teacher.role or '').lower() == 'hod':
        return True
    if not _is_dept_mode():
        return True
    from db import SubjectLead
    if subject:
        if SubjectLead.query.filter_by(teacher_id=teacher.id, subject=subject).first():
            return True
    else:
        if SubjectLead.query.filter_by(teacher_id=teacher.id).first():
            return True
    return False


@app.route('/teacher/subject-standards')
def teacher_subject_standards_page():
    teacher = _current_teacher()
    if not _can_edit_subject_standards(teacher):
        return jsonify({'error': 'forbidden'}), 403
    return render_template('subject_standards.html', teacher=teacher)
```

If `_is_dept_mode` already exists with a different name (e.g., `dept_mode_enabled`), use the existing helper instead.

- [ ] **Step 4: Create minimal template**

`templates/subject_standards.html`:

```jinja
{% extends "base.html" %}
{% block content %}
<div class="page subject-standards">
  <h2>Subject standards</h2>

  <section class="pending-queue">
    <h3>Pending review</h3>
    <div id="pending-list">Loading…</div>
  </section>

  <section class="active-standards">
    <h3>Active standards</h3>
    <div class="filters">
      <select id="filter-subject"><option value="">All subjects</option></select>
      <input id="filter-search" placeholder="Search text…" />
    </div>
    <div id="active-list">Loading…</div>
  </section>
</div>

<script>
fetch('/api/subject_standards?status=pending_review')
    .then(function (r) { return r.json(); })
    .then(function (data) {
        document.getElementById('pending-list').textContent =
            data.standards.length + ' pending';
    });
</script>
{% endblock %}
```

- [ ] **Step 5: Add JSON listing endpoint**

```python
@app.route('/api/subject_standards', methods=['GET'])
def api_subject_standards_list():
    from db import SubjectStandard
    teacher = _current_teacher()
    if not _can_edit_subject_standards(teacher):
        return jsonify({'error': 'forbidden'}), 403
    status = request.args.get('status', 'active')
    subject_filter = request.args.get('subject')
    q = SubjectStandard.query.filter_by(status=status)
    if subject_filter:
        q = q.filter_by(subject=subject_filter)
    rows = q.order_by(SubjectStandard.reinforcement_count.desc()).all()
    return jsonify({
        'standards': [
            {
                'id': r.id,
                'uuid': r.uuid,
                'subject': r.subject,
                'text': r.text,
                'topic_keys': json.loads(r.topic_keys or '[]'),
                'theme_key': r.theme_key,
                'reinforcement_count': r.reinforcement_count,
                'status': r.status,
                'created_at': r.created_at.isoformat() if r.created_at else None,
                'updated_at': r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    })
```

- [ ] **Step 6: Run — expect PASS**

- [ ] **Step 7: Commit**

```bash
git add app.py templates/subject_standards.html tests/test_subject_standards.py
git commit -m "feat(standards): subject standards page with permission gate"
```

### Task 10.2: Approve / Edit / Reject actions

- [ ] **Step 1: Write failing tests**

```python
def test_approve_moves_pending_to_active(app, db_session, client):
    from db import SubjectStandard, Teacher
    t = Teacher(id='t-hod-a', name='HOD', access_code='HODA', role='hod')
    db_session.add(t)
    s = SubjectStandard(subject='biology', text='X', topic_keys='["enzymes"]',
                        status='pending_review', created_by=t.id)
    db_session.add(s)
    db_session.commit()
    sid = s.id
    with client.session_transaction() as sess:
        sess['teacher_id'] = t.id
        sess['authenticated'] = True
    rv = client.post(f'/api/subject_standards/{sid}/approve')
    assert rv.status_code == 200
    db_session.refresh(s)
    assert s.status == 'active'
    assert s.reviewed_by == t.id


def test_edit_updates_text_and_bumps_updated_at(app, db_session, client):
    from db import SubjectStandard, Teacher
    t = Teacher(id='t-hod-b', name='HOD', access_code='HODB', role='hod')
    db_session.add(t)
    s = SubjectStandard(subject='biology', text='Old', topic_keys='[]',
                        status='active', created_by=t.id)
    db_session.add(s)
    db_session.commit()
    old_updated = s.updated_at
    with client.session_transaction() as sess:
        sess['teacher_id'] = t.id
        sess['authenticated'] = True
    rv = client.post(f'/api/subject_standards/{s.id}/edit', json={'text': 'New text'})
    assert rv.status_code == 200
    db_session.refresh(s)
    assert s.text == 'New text'
    assert s.updated_at > old_updated


def test_reject_archives_standard(app, db_session, client):
    from db import SubjectStandard, Teacher
    t = Teacher(id='t-hod-c', name='HOD', access_code='HODC', role='hod')
    db_session.add(t)
    s = SubjectStandard(subject='biology', text='X', topic_keys='[]',
                        status='pending_review', created_by=t.id)
    db_session.add(s)
    db_session.commit()
    with client.session_transaction() as sess:
        sess['teacher_id'] = t.id
        sess['authenticated'] = True
    rv = client.post(f'/api/subject_standards/{s.id}/reject')
    assert rv.status_code == 200
    db_session.refresh(s)
    assert s.status == 'archived'
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add the three routes**

```python
def _load_standard_or_404(standard_id):
    from db import SubjectStandard
    s = SubjectStandard.query.get(standard_id)
    if s is None:
        return None, (jsonify({'error': 'not_found'}), 404)
    return s, None


@app.route('/api/subject_standards/<int:standard_id>/approve', methods=['POST'])
def api_subject_standards_approve(standard_id):
    s, err = _load_standard_or_404(standard_id)
    if err: return err
    teacher = _current_teacher()
    if not _can_edit_subject_standards(teacher, subject=s.subject):
        return jsonify({'error': 'forbidden'}), 403
    s.status = 'active'
    s.reviewed_by = teacher.id
    s.reviewed_at = datetime.now(timezone.utc)
    s.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/subject_standards/<int:standard_id>/edit', methods=['POST'])
def api_subject_standards_edit(standard_id):
    s, err = _load_standard_or_404(standard_id)
    if err: return err
    teacher = _current_teacher()
    if not _can_edit_subject_standards(teacher, subject=s.subject):
        return jsonify({'error': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    new_text = (data.get('text') or '').strip()
    if not new_text:
        return jsonify({'error': 'text required'}), 400
    s.text = new_text
    s.reviewed_by = teacher.id
    s.reviewed_at = datetime.now(timezone.utc)
    s.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'success': True, 'text': new_text})


@app.route('/api/subject_standards/<int:standard_id>/reject', methods=['POST'])
def api_subject_standards_reject(standard_id):
    s, err = _load_standard_or_404(standard_id)
    if err: return err
    teacher = _current_teacher()
    if not _can_edit_subject_standards(teacher, subject=s.subject):
        return jsonify({'error': 'forbidden'}), 403
    s.status = 'archived'
    s.reviewed_by = teacher.id
    s.reviewed_at = datetime.now(timezone.utc)
    s.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'success': True})
```

- [ ] **Step 4: Add row rendering + bind buttons in `subject_standards.html`**

Inside the `<script>` block:

```javascript
function escapeHtml(s) {
    return (s || '').replace(/[&<>]/g, function (c) {
        return ({'&':'&amp;','<':'&lt;','>':'&gt;'})[c];
    });
}

function renderPending(rows) {
    var html = '';
    rows.forEach(function (r) {
        html += '<div class="ss-row" data-id="' + r.id + '">' +
            '<p>' + escapeHtml(r.text) + '</p>' +
            '<p class="muted">' + r.subject + ' • ' + (r.topic_keys || []).join(', ') +
            ' • reinforced ' + r.reinforcement_count + '×</p>' +
            '<button data-action="approve" data-id="' + r.id + '">Approve</button> ' +
            '<button data-action="reject" data-id="' + r.id + '">Reject</button> ' +
            '<button data-action="edit" data-id="' + r.id + '">Edit</button>' +
            '<div class="related" id="related-' + r.id + '"></div>' +
            '</div>';
    });
    document.getElementById('pending-list').innerHTML = html || '(none pending)';
    bindActions();
    rows.forEach(function (r) { fetchRelated(r.id); });
}

function bindActions() {
    document.querySelectorAll('[data-action="approve"]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            fetch('/api/subject_standards/' + btn.dataset.id + '/approve', { method: 'POST' })
                .then(function () { location.reload(); });
        });
    });
    document.querySelectorAll('[data-action="reject"]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            fetch('/api/subject_standards/' + btn.dataset.id + '/reject', { method: 'POST' })
                .then(function () { location.reload(); });
        });
    });
    document.querySelectorAll('[data-action="edit"]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var current = btn.parentElement.querySelector('p').textContent;
            var next = prompt('Edit standard text:', current);
            if (next && next.trim()) {
                fetch('/api/subject_standards/' + btn.dataset.id + '/edit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: next.trim() }),
                }).then(function () { location.reload(); });
            }
        });
    });
}

function fetchRelated(standardId) {
    fetch('/api/subject_standards/' + standardId + '/related')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            var el = document.getElementById('related-' + standardId);
            if (!el || !data.related || !data.related.length) return;
            var html = '<p class="muted">Related existing standards:</p><ul>';
            data.related.forEach(function (rs) {
                html += '<li>' + escapeHtml(rs.text) + ' (reinforced ' + rs.reinforcement_count + '×)</li>';
            });
            html += '</ul>';
            el.innerHTML = html;
        });
}

fetch('/api/subject_standards?status=pending_review')
    .then(function (r) { return r.json(); })
    .then(function (data) { renderPending(data.standards); });
```

- [ ] **Step 5: Run — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add app.py templates/subject_standards.html tests/test_subject_standards.py
git commit -m "feat(standards): approve / edit / reject endpoints + UI bindings"
```

### Task 10.3: "Related existing standards" panel

- [ ] **Step 1: Add failing test**

```python
def test_related_endpoint_returns_overlapping_active_standards(app, db_session, client):
    from db import SubjectStandard, Teacher
    t = Teacher(id='t-r', name='r', access_code='R1', role='hod')
    db_session.add(t)
    pending = SubjectStandard(subject='biology', text='Reject heat',
                              topic_keys='["enzymes", "terminology_precision"]',
                              status='pending_review', created_by=t.id)
    active = SubjectStandard(subject='biology', text='Accept temperature',
                             topic_keys='["enzymes"]',
                             status='active', created_by=t.id, reinforcement_count=4)
    other = SubjectStandard(subject='biology', text='Genetics rule',
                            topic_keys='["genetics"]',
                            status='active', created_by=t.id)
    db_session.add_all([pending, active, other])
    db_session.commit()
    with client.session_transaction() as sess:
        sess['teacher_id'] = t.id
        sess['authenticated'] = True
    rv = client.get(f'/api/subject_standards/{pending.id}/related')
    assert rv.status_code == 200
    payload = rv.get_json()
    ids = [s['id'] for s in payload['related']]
    assert active.id in ids
    assert other.id not in ids
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add `find_related_standards` to `subject_standards.py`**

```python
def find_related_standards(standard, limit=5):
    """Active standards on the same subject with overlapping topic_keys.
    Excludes the input row itself."""
    candidates = (
        SubjectStandard.query
        .filter(SubjectStandard.subject == standard.subject,
                SubjectStandard.status == 'active',
                SubjectStandard.id != standard.id)
        .order_by(SubjectStandard.reinforcement_count.desc())
        .all()
    )
    own_keys = set(json.loads(standard.topic_keys or '[]'))
    if not own_keys:
        return []
    out = []
    for c in candidates:
        c_keys = set(json.loads(c.topic_keys or '[]'))
        if c_keys & own_keys:
            out.append(c)
            if len(out) >= limit:
                break
    return out
```

- [ ] **Step 4: Add endpoint**

```python
@app.route('/api/subject_standards/<int:standard_id>/related', methods=['GET'])
def api_subject_standards_related(standard_id):
    s, err = _load_standard_or_404(standard_id)
    if err: return err
    teacher = _current_teacher()
    if not _can_edit_subject_standards(teacher, subject=s.subject):
        return jsonify({'error': 'forbidden'}), 403
    from subject_standards import find_related_standards
    related = find_related_standards(s)
    return jsonify({
        'related': [
            {
                'id': r.id, 'text': r.text,
                'topic_keys': json.loads(r.topic_keys or '[]'),
                'reinforcement_count': r.reinforcement_count,
                'status': r.status,
            }
            for r in related
        ],
    })
```

- [ ] **Step 5: Run — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add app.py subject_standards.py tests/test_subject_standards.py
git commit -m "feat(standards): related existing standards panel on review"
```

---

## Phase 11: Migration runtime — 5-day cutoff classifier

### Task 11.1: One-shot boot-time migration

- [ ] **Step 1: Write failing tests**

Append to `tests/test_migration_calibration.py`:

```python
from datetime import datetime, timezone, timedelta
from db import Assignment, FeedbackEdit, MarkingPrinciplesCache


def test_legacy_assignments_get_legacy_status(app, db_session):
    old = Assignment(id='old-1', classroom_code='OLD1', subject='biology',
                     title='Old', created_at=datetime.now(timezone.utc) - timedelta(days=30))
    new = Assignment(id='new-1', classroom_code='NEW1', subject='biology',
                     title='New', created_at=datetime.now(timezone.utc) - timedelta(days=2))
    db_session.add_all([old, new])
    db_session.commit()

    from db import _migrate_calibration_runtime
    _migrate_calibration_runtime(_app=__import__('app').app, force=True)

    db_session.refresh(old)
    db_session.refresh(new)
    assert old.topic_keys_status == 'legacy'
    assert new.topic_keys_status == 'pending'


def test_legacy_feedback_edits_deactivated(app, db_session):
    from db import Teacher
    t = Teacher(id='t-mig', name='Joe', access_code='MIG1', role='teacher')
    db_session.add(t)
    old_asn = Assignment(id='old-2', classroom_code='OLD2', subject='biology',
                         title='Old', created_at=datetime.now(timezone.utc) - timedelta(days=30))
    new_asn = Assignment(id='new-2', classroom_code='NEW2', subject='biology',
                         title='New', created_at=datetime.now(timezone.utc) - timedelta(days=2))
    db_session.add_all([old_asn, new_asn])
    db_session.commit()
    old_fe = FeedbackEdit(submission_id=1, criterion_id='1', field='feedback',
                          original_text='x', edited_text='y',
                          edited_by=t.id, assignment_id=old_asn.id,
                          rubric_version='v1', scope='individual', active=True)
    new_fe = FeedbackEdit(submission_id=2, criterion_id='1', field='feedback',
                          original_text='x', edited_text='y',
                          edited_by=t.id, assignment_id=new_asn.id,
                          rubric_version='v1', scope='individual', active=True)
    db_session.add_all([old_fe, new_fe])
    db_session.commit()

    from db import _migrate_calibration_runtime
    _migrate_calibration_runtime(_app=__import__('app').app, force=True)

    db_session.refresh(old_fe)
    db_session.refresh(new_fe)
    assert old_fe.active is False
    assert new_fe.active is True
    assert new_fe.amend_answer_key is True
    assert new_fe.scope == 'amendment'
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add `MigrationFlag` model + `_migrate_calibration_runtime` to `db.py`**

Above `class MarkingPrinciplesCache`, add:

```python
class MigrationFlag(db.Model):
    """One-shot migration marker. Each named migration writes a single row
    here on completion to prevent re-running."""
    __tablename__ = 'migration_flag'
    name = db.Column(db.String(80), primary_key=True)
    applied_at = db.Column(db.DateTime(timezone=True),
                            default=lambda: datetime.now(timezone.utc),
                            nullable=False)
```

At module level (after the imports):

```python
_CALIBRATION_RUNTIME_MIGRATION_NAME = 'calibration_runtime_2026_05_13'


def _migrate_calibration_runtime(_app, force=False):
    """One-shot migration applying the 5-day cutoff classification rules.
    Spec: docs/superpowers/specs/2026-05-13-calibration-edit-intent-design.md §7
    """
    from datetime import timedelta as _td
    with _app.app_context():
        marker = MigrationFlag.query.filter_by(name=_CALIBRATION_RUNTIME_MIGRATION_NAME).first()
        if marker is not None and not force:
            return

        cutoff = datetime.now(timezone.utc) - _td(days=5)

        # Classify assignments
        for asn in Assignment.query.all():
            if asn.topic_keys_status == 'tagged':
                continue  # already onboarded post-deploy
            asn_created = asn.created_at
            if asn_created is None:
                asn.topic_keys_status = 'legacy'
                continue
            if asn_created.tzinfo is None:
                asn_created = asn_created.replace(tzinfo=timezone.utc)
            asn.topic_keys_status = 'pending' if asn_created >= cutoff else 'legacy'

        db.session.commit()

        # Classify FeedbackEdits
        for fe in FeedbackEdit.query.filter_by(active=True).all():
            parent = Assignment.query.get(fe.assignment_id)
            if parent is None:
                fe.active = False
                continue
            if parent.topic_keys_status == 'legacy':
                fe.active = False
            else:
                fe.amend_answer_key = True
                fe.scope = 'amendment'

        # Deactivate MarkingPrinciplesCache
        db.session.query(MarkingPrinciplesCache).update({'is_stale': True})

        db.session.commit()

        if marker is None:
            db.session.add(MigrationFlag(name=_CALIBRATION_RUNTIME_MIGRATION_NAME))
            db.session.commit()
```

- [ ] **Step 4: Wire into `init_db`**

In `init_db`, after `_migrate_add_columns(app)` and `seed_subject_topic_vocabulary()`, add:

```python
        _migrate_calibration_runtime(app)
```

- [ ] **Step 5: Run — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_migration_calibration.py
git commit -m "feat(migration): 5-day cutoff classifier + legacy edit deactivation"
```

---

## Phase 12: Lazy topic tagging

### Task 12.1: Tag on first open of `pending` assignment

- [ ] **Step 1: Write failing test**

```python
def test_first_open_of_pending_assignment_triggers_tagging(app, db_session, client):
    from db import Teacher, Assignment
    from unittest.mock import patch
    t = Teacher(id='t-lazy', name='Joe', access_code='LAZ1', role='owner')
    asn = Assignment(id='lazy-1', classroom_code='LZY', subject='biology',
                     title='Bio Test', topic_keys_status='pending',
                     teacher_id=t.id, provider='anthropic', model='claude-sonnet-4-6')
    db_session.add_all([t, asn])
    db_session.commit()
    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    with patch('ai_marking.extract_assignment_topic_keys',
              return_value=[['enzymes']]) as mock_extract:
        rv = client.get(f'/teacher/assignment/{asn.id}')
    assert mock_extract.call_count >= 1
    db_session.refresh(asn)
    assert asn.topic_keys_status == 'tagged'
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add the trigger in the assignment-detail route**

Find the existing route (`grep -n "def teacher_assignment_page\|GET.*assignment" app.py`). Near the top, after ownership check, add:

```python
if asn.topic_keys_status == 'pending':
    try:
        _kick_off_topic_tagging(asn)
    except Exception as e:
        logger.warning(f'lazy topic tagging failed for {asn.id}: {e}')
```

Add helper:

```python
def _kick_off_topic_tagging(asn):
    """Synchronous topic tagging using Haiku. Exceptions swallowed — marking
    is never blocked by tagging (spec §2 Additive, not gating)."""
    from ai_marking import extract_assignment_topic_keys, _helper_model_for, _decode_answer_key_text
    # Question-paper text extraction (reuse existing helper if present, else minimal).
    qp_text = ''
    ak_text = ''
    try:
        if asn.question_paper:
            qp_text = _decode_answer_key_text(asn.question_paper) or ''
        if asn.answer_key:
            ak_text = _decode_answer_key_text(asn.answer_key) or ''
    except Exception:
        pass

    questions = _split_into_questions(qp_text, ak_text)
    if not questions:
        return
    provider = asn.provider or 'anthropic'
    helper_model = _helper_model_for(provider, fallback=asn.model)
    keys = extract_assignment_topic_keys(
        provider=provider, model=helper_model,
        session_keys=asn.get_api_keys() or {},
        subject=(asn.subject or '').strip().lower(),
        questions=questions,
    )
    asn.topic_keys = json.dumps(keys)
    asn.topic_keys_status = 'tagged'
    db.session.commit()


def _split_into_questions(qp_text, ak_text):
    """Cheap regex split on Q1/Q2/... markers. Returns list of
    {'question_num', 'text', 'answer_key'} dicts. Returns [] if no markers
    found — caller should gracefully skip tagging."""
    import re
    if not qp_text and not ak_text:
        return []
    qp_blocks = re.split(r'(?im)^\s*Q\s*(\d+)\b[:.)\s]', qp_text or '')
    ak_blocks = re.split(r'(?im)^\s*Q\s*(\d+)\b[:.)\s]', ak_text or '')

    def _to_dict(blocks):
        # re.split returns [leading, num1, body1, num2, body2, ...]
        out = {}
        for i in range(1, len(blocks), 2):
            try:
                num = int(blocks[i])
            except (ValueError, TypeError):
                continue
            out[num] = (blocks[i + 1] if i + 1 < len(blocks) else '').strip()
        return out

    qp_map = _to_dict(qp_blocks)
    ak_map = _to_dict(ak_blocks)
    all_qs = sorted(set(qp_map) | set(ak_map))
    return [
        {'question_num': qn, 'text': qp_map.get(qn, ''), 'answer_key': ak_map.get(qn, '')}
        for qn in all_qs
    ]
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_subject_standards.py
git commit -m "feat(tagging): lazy topic tagging on first open of pending assignment"
```

---

## Phase 13: Rubric re-upload carry-over

### Task 13.1: Detect re-upload and re-pin amendments

- [ ] **Step 1: Locate the upload route**

```bash
grep -n "asn.rubrics =\|asn.answer_key =\|upload.rubric\|upload.answer.key" /Users/changshien/Documents/Github/ai-marking-prototype/app.py | head -10
```

- [ ] **Step 2: Write failing test**

Create `tests/test_rubric_reupload.py`:

```python
"""UP-: rubric re-upload auto-carries amendments to new rubric_version."""

from io import BytesIO
from db import db, Teacher, Assignment, FeedbackEdit


def test_reupload_carries_amendments_to_new_rubric_version(app, db_session, client):
    t = Teacher(id='t-ru', name='Joe', access_code='RU1', role='owner')
    asn = Assignment(id='ru-1', classroom_code='RUU', subject='biology',
                     title='Bio', teacher_id=t.id,
                     rubrics=b'old-rubric-bytes', topic_keys_status='tagged')
    db_session.add_all([t, asn])
    db_session.commit()
    from ai_marking import _rubric_version_hash
    old_rv = _rubric_version_hash(asn)
    db_session.add(FeedbackEdit(
        submission_id=1, criterion_id='3', field='feedback',
        original_text='X', edited_text='Accept powerhouse',
        edited_by=t.id, assignment_id=asn.id,
        rubric_version=old_rv, scope='amendment',
        amend_answer_key=True, active=True,
    ))
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv = client.post(
        f'/teacher/assignment/{asn.id}/upload-rubric',
        data={'rubric': (BytesIO(b'new-rubric-bytes-different'), 'rubric.pdf')},
        content_type='multipart/form-data',
    )
    assert rv.status_code == 200
    db_session.refresh(asn)
    new_rv = _rubric_version_hash(asn)
    assert new_rv != old_rv
    fe = FeedbackEdit.query.filter_by(assignment_id=asn.id, active=True).first()
    assert fe is not None
    assert fe.rubric_version == new_rv
```

- [ ] **Step 3: Run — expect FAIL**

- [ ] **Step 4: Add carry-over to the upload route**

After the existing block that writes the new rubric bytes and commits, add:

```python
from ai_marking import _rubric_version_hash
new_rv = _rubric_version_hash(asn)
edits = FeedbackEdit.query.filter_by(
    assignment_id=asn.id, active=True, amend_answer_key=True,
).all()
carried = 0
for fe in edits:
    if fe.rubric_version != new_rv:
        fe.rubric_version = new_rv
        carried += 1
db.session.commit()
```

Add `carried_amendments=carried` into the existing return shape (jsonify or template).

- [ ] **Step 5: Frontend banner**

In the assignment template's existing upload-response handler:

```javascript
if (response.carried_amendments && response.carried_amendments > 0) {
    var banner = document.createElement('div');
    banner.className = 'banner banner-info';
    banner.textContent = response.carried_amendments +
        ' amendments carried over from previous rubric. ';
    var link = document.createElement('a');
    link.textContent = 'Review them';
    link.href = '#amendments-list';
    banner.appendChild(link);
    document.body.insertBefore(banner, document.body.firstChild);
}
```

- [ ] **Step 6: Run — expect PASS**

- [ ] **Step 7: Commit**

```bash
git add app.py templates/<file>.html tests/test_rubric_reupload.py
git commit -m "feat(rubric): auto-carry amendments to new rubric_version on re-upload"
```

---

## Phase 14: Hide teacher theme_key UI

### Task 14.1: Env-var gate

- [ ] **Step 1: Add `TEACHER_THEME_UI_ENABLED` env-var read in `app.py`**

Find where existing env-var booleans live (search for `STUDENT_GROUPING_UI_ENABLED`):

```python
TEACHER_THEME_UI_ENABLED = (
    os.environ.get('TEACHER_THEME_UI_ENABLED', 'FALSE').upper() == 'TRUE'
)
```

- [ ] **Step 2: Surface flag to Jinja via context processor**

```python
@app.context_processor
def _inject_feature_flags():
    return {
        'TEACHER_THEME_UI_ENABLED': TEACHER_THEME_UI_ENABLED,
    }
```

If an existing context processor injects flags, add to that one instead of creating a duplicate.

- [ ] **Step 3: Inject global in `templates/base.html`**

In the existing `<script>` block where window globals live:

```jinja
<script>
window.TEACHER_THEME_UI_ENABLED = {{ TEACHER_THEME_UI_ENABLED|tojson }};
</script>
```

- [ ] **Step 4: Gate the JS render**

In `static/js/feedback_render.js`, find `renderTriggerInner` (around line 1528) and the call site around lines 1220–1246. At the outermost entry, add:

```javascript
if (window.TEACHER_THEME_UI_ENABLED !== true) {
    // Hide the theme-key edit trigger entirely. Underlying data still flows.
    return '';  // or skip the appendChild — whichever matches existing style
}
```

- [ ] **Step 5: Document the env var**

In `CLAUDE.md`, in the env-var table, add:

```
| `TEACHER_THEME_UI_ENABLED` | `TRUE` re-enables the teacher-facing inline theme/category dropdown on each marked criterion. Default `FALSE` since categorisation accuracy has proven sufficient. The data pipeline (theme_key on criteria, FeedbackEdit inheritance, calibration retrieval) runs regardless — this flag only controls the teacher correction UI surface. Parallel to `STUDENT_GROUPING_UI_ENABLED`. |
```

- [ ] **Step 6: Manual verification**

Default run:

```bash
python app.py
```

Open a marked submission. Verify the inline theme dropdown is hidden but feedback editing and the two intent checkboxes still work.

Then:

```bash
TEACHER_THEME_UI_ENABLED=TRUE python app.py
```

Verify the dropdown reappears.

- [ ] **Step 7: Commit**

```bash
git add app.py static/js/feedback_render.js templates/base.html CLAUDE.md
git commit -m "feat(ui): gate teacher theme_key dropdown behind TEACHER_THEME_UI_ENABLED env var"
```

---

## Phase 15: Export endpoint

### Task 15.1: Streamed JSONL export

- [ ] **Step 1: Write failing test**

```python
def test_export_jsonl_streams_active_standards(app, db_session, client):
    from db import SubjectStandard, Teacher
    t = Teacher(id='t-exp', name='HOD', access_code='EXP1', role='hod')
    db_session.add(t)
    db_session.add(SubjectStandard(
        subject='biology', text='Accept temperature', topic_keys='["enzymes"]',
        theme_key='terminology_precision', status='active', created_by=t.id,
        reinforcement_count=5,
    ))
    db_session.commit()
    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv = client.get('/api/subject_standards/export?subject=biology&format=jsonl')
    assert rv.status_code == 200
    assert rv.mimetype == 'application/x-ndjson'
    lines = rv.data.decode().strip().split('\n')
    assert len(lines) == 1
    import json as _json
    payload = _json.loads(lines[0])
    assert payload['content'] == 'Accept temperature'
    assert payload['metadata']['subject_key'] == 'biology'
    assert payload['metadata']['topic_keys'] == ['enzymes']
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add the endpoint**

```python
@app.route('/api/subject_standards/export', methods=['GET'])
def api_subject_standards_export():
    teacher = _current_teacher()
    subject = request.args.get('subject', '').strip().lower()
    if not subject:
        return jsonify({'error': 'subject required'}), 400
    if not _can_edit_subject_standards(teacher, subject=subject):
        return jsonify({'error': 'forbidden'}), 403
    updated_since_raw = request.args.get('updated_since')
    updated_since = None
    if updated_since_raw:
        try:
            updated_since = datetime.fromisoformat(updated_since_raw.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'error': 'invalid updated_since'}), 400

    def generate():
        from db import SubjectStandard, Teacher as _T
        q = SubjectStandard.query.filter_by(subject=subject, status='active')
        if updated_since:
            q = q.filter(SubjectStandard.updated_at >= updated_since)
        for r in q.yield_per(100):
            creator = _T.query.get(r.created_by) if r.created_by else None
            reviewer = _T.query.get(r.reviewed_by) if r.reviewed_by else None
            row = {
                'id': r.uuid,
                'content': r.text,
                'metadata': {
                    'subject_key': r.subject,
                    'subject_display': r.subject.title() if r.subject else '',
                    'topic_keys': json.loads(r.topic_keys or '[]'),
                    'theme_key': r.theme_key,
                    'reinforcement_count': r.reinforcement_count,
                    'status': r.status,
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                    'updated_at': r.updated_at.isoformat() if r.updated_at else None,
                    'created_by': {'name': creator.name, 'role': creator.role} if creator else None,
                    'reviewed_by': {'name': reviewer.name, 'role': reviewer.role} if reviewer else None,
                },
            }
            yield json.dumps(row) + '\n'

    return Response(generate(), mimetype='application/x-ndjson')
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_subject_standards.py
git commit -m "feat(export): JSONL streaming export endpoint for subject standards"
```

---

## Phase 16: Integration + polish

### Task 16.1: End-to-end happy-path integration test

- [ ] **Step 1: Write the integration test**

Append to `tests/test_calibration_intent.py`:

```python
def test_full_happy_path_amend_then_promote_then_retrieve(app, db_session, client):
    """End-to-end: tagged assignment → teacher edits with both intents →
    promotion → approved → retrieval pulls it on next marking."""
    from unittest.mock import patch
    from db import Teacher, Assignment, Submission, SubjectStandard
    from subject_standards import retrieve_subject_standards
    import json

    t = Teacher(id='t-e2e', name='Joe', access_code='E2E1', role='hod')
    asn = Assignment(id='e2e', classroom_code='E2E', subject='biology', title='Bio',
                     teacher_id=t.id, topic_keys=json.dumps([['enzymes', 'terminology_precision']]),
                     topic_keys_status='tagged', provider='anthropic',
                     model='claude-sonnet-4-6')
    sub = Submission(assignment_id=asn.id, student_id=None,
                     result_json=json.dumps({'questions': [
                         {'question_num': 1, 'feedback': 'Correct — heat affects enzyme rate.',
                          'theme_key': 'terminology_precision'},
                     ]}),
                     provider='anthropic', model='claude-sonnet-4-6')
    db_session.add_all([t, asn, sub])
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes', 'terminology_precision']):
        rv = client.patch(
            f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
            json={'questions': [{'question_num': 1,
                                  'feedback': "Must say 'temperature', not 'heat'.",
                                  'amend_answer_key': True,
                                  'update_subject_standards': True}]},
        )
    assert rv.status_code == 200

    ss = SubjectStandard.query.filter_by(subject='biology').first()
    assert ss is not None
    assert ss.status == 'pending_review'

    rv = client.post(f'/api/subject_standards/{ss.id}/approve')
    assert rv.status_code == 200
    db_session.refresh(ss)
    assert ss.status == 'active'

    out = retrieve_subject_standards(
        subject='biology',
        per_question_topic_keys=[['enzymes']],
    )
    assert any('temperature' in s.text for s in out)
```

- [ ] **Step 2: Run — expect PASS**

```bash
pytest tests/test_calibration_intent.py::test_full_happy_path_amend_then_promote_then_retrieve -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_calibration_intent.py
git commit -m "test(integration): end-to-end happy path for calibration intent + promotion"
```

### Task 16.2: Run the full test suite, fix regressions

- [ ] **Step 1: Run the full suite**

```bash
pytest tests/ -v
```

- [ ] **Step 2: Investigate any failures**

For each failure, determine:
1. **Real regression** in code we modified → fix.
2. **Test referenced old `calibrate=` API** → update the test stub to the new flag names.
3. **Unrelated flaky test** → re-run; if persistent, log it but don't fix in this branch.

- [ ] **Step 3: Commit fixes individually**

```bash
git add <fixed files>
git commit -m "fix(<scope>): <one-line>"
```

### Task 16.3: Manual verification of the full workflow

- [ ] **Step 1: Start the dev server**

```bash
python app.py
```

- [ ] **Step 2: Walk the golden path**

In a browser with an HOD-roled session, verify in order:

1. Settings → "Subject standards" link visible
2. Create a new assignment with subject=Biology, upload question paper + answer key
3. Open the assignment — Topic tags section appears, status moves from `pending` to `tagged` within ~15s
4. Mark a script
5. Open the result, edit a feedback field, tick "Update subject standards", save
6. Visit Settings → Subject standards — new pending row appears with "Related existing standards" panel
7. Click Approve — row moves to Active
8. Mark another script in the same assignment; verify (via dev-time logging) the new standard appears in the prompt
9. Edit a feedback field, tick only "Amend answer key", save; verify "Preview effective answer key" shows the clarification appended
10. Click "Update bank version with my amendments"; verify bank's stored answer key updates
11. With default env, verify the inline theme dropdown is hidden in the result view

If any step fails, **stop, investigate, fix, and commit before continuing.**

---

## Phase 17: Final commit + documentation

### Task 17.1: Update CLAUDE.md with calibration architecture notes

- [ ] **Step 1: Verify the env var documentation from Phase 14 is present**

- [ ] **Step 2: Add a "Calibration system" subsection under Architecture**

Append to `CLAUDE.md`:

```markdown
### Calibration system (subject standards)

Calibration edits split by intent at save time:
- **Amend answer key** — `FeedbackEdit.amend_answer_key=true`, scoped to the assignment. Merged into the effective answer key on every marking job for that assignment via `subject_standards.build_effective_answer_key`.
- **Update subject standards** — promoted to `SubjectStandard` via `subject_standards.promote_to_subject_standard`. AI-tagged with topic_keys from `config/subject_topics/<subject>.py`. Requires HOD / subject-lead approval before going active.

Marking-time retrieval is `subject_standards.retrieve_subject_standards` — topic overlap with the assignment's per-question topic_keys, per-topic quota of 3, hard cap of 30. Bank size is effectively unbounded; prompt size stays constant.

Migration (one-shot at boot, see `db._migrate_calibration_runtime`):
- Assignments older than 5 days at deploy → `topic_keys_status='legacy'`; FeedbackEdits on them deactivated.
- Assignments within 5 days → `topic_keys_status='pending'`; lazy AI tagging on first open; FeedbackEdits converted to `amend_answer_key=true` amendments.
- The standards bank always starts empty.

`MarkingPrinciplesCache` is deprecated — table preserved for audit but no longer regenerated or applied.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): add calibration system architecture notes + TEACHER_THEME_UI_ENABLED"
```

---

## Self-review checklist (run before declaring done)

- [ ] All 11 sections of the spec are covered by at least one task. (§1–§11)
- [ ] No placeholders in any task body — no "TBD", no "implement later", no "similar to Task N".
- [ ] Type/method names are consistent across tasks: `extract_assignment_topic_keys`, `extract_standard_topic_keys`, `promote_to_subject_standard`, `retrieve_subject_standards`, `build_effective_answer_key`, `find_similar_standard`, `find_related_standards`, `_can_edit_subject_standards`, `_kick_off_topic_tagging`, `_split_into_questions`.
- [ ] Every commit message follows `type(scope): summary` style matching the repo's existing pattern.
- [ ] The full test suite passes (`pytest tests/ -v`).
- [ ] Manual UI walkthrough (Phase 16 Task 16.3) completed without surprises.
