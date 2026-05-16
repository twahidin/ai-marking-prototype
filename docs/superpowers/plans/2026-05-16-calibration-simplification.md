# Calibration Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove topic tagging, the subject-standards pipeline, and the second "Update subject standards" checkbox; keep only the "Amend answer key/rubric" calibration intent. Fix two correctness bugs in propagation: route the Haiku re-mark to the correct field, and allow it to update `marks_awarded`.

**Architecture:** Four sequential commits on `sandbox_upgraded` only. (1) JS one-checkbox UI. (2) Field-aware + marks-aware propagation backend with TDD. (3) Backend deletions (routes, models, helpers, configs, templates, tests). (4) Schema migration with raw SQL DROP TABLE / DROP COLUMN and a new `_drop_columns` helper that handles both SQLite and Postgres.

**Tech Stack:** Python 3.10+, Flask, SQLAlchemy, pytest, vanilla JS. SQLite (dev), Postgres (prod via `DATABASE_URL`).

**Spec:** [`docs/superpowers/specs/2026-05-16-calibration-simplification-design.md`](../specs/2026-05-16-calibration-simplification-design.md)

**Branch policy:** All commits land on `sandbox_upgraded`. Never push to `staging`. Per CLAUDE.md: parallel cherry-picked histories — never merge across.

---

## File Structure

**Files modified per commit:**

| Commit | File | Change type |
|---|---|---|
| 1 | `static/js/feedback_render.js` | Modify — remove second checkbox, simplify save payload, simplify indicator tag |
| 1 | `app.py` (lines ~8722, ~8736) | Modify — drop `update_subject_standards` from `text_edit_meta` response |
| 2 | `ai_marking.py` (lines 1887-1932) | Modify — rewrite `refresh_criterion_feedback` (field-aware, marks-aware) |
| 2 | `app.py` (lines 6594-6611) | Modify — `_run_propagation_worker` passes `target_field`, routes result |
| 2 | `app.py` (lines 9069-9202) | Modify — drop `promote_flag` plumbing, simplify FeedbackEdit write |
| 2 | `tests/test_propagation.py` | Create — TDD tests for field routing + marks update |
| 3 | `templates/subject_standards.html` | Delete |
| 3 | `config/subject_topics/*.py` (9 files) | Delete |
| 3 | `subject_standards.py` | Modify — shrink to just `build_effective_answer_key` |
| 3 | `ai_marking.py` | Modify — remove 3 topic-extraction functions |
| 3 | `app.py` | Modify — remove 8 routes + `_kick_off_topic_tagging` + `_can_edit_subject_standards` + `_normalize_topic_keys`; shrink `_build_calibration_block_for` |
| 3 | `db.py` | Modify — remove `SubjectStandard`, `SubjectTopicVocabulary` model classes; remove `_seed_calibration_intent_assignments`; remove topic_keys_status legacy backfill from `_migrate_calibration_runtime` |
| 3 | `tests/test_subject_standards.py` | Delete |
| 3 | `tests/test_subject_topic_vocab.py` | Delete |
| 3 | `tests/test_calibration_intent.py` | Modify — refactor to amend-only |
| 3 | `CLAUDE.md` | Modify — remove topic-tagging + subject-standards sections |
| 4 | `db.py` | Modify — add `_drop_columns` helper + `_migrate_drop_subject_standards` |
| 4 | `tests/test_migration_calibration.py` | Modify — add migration assertions |

---

## Commit 1: UI — single-checkbox calibration

**Files:**
- Modify: `static/js/feedback_render.js:1798-1850, 1862-1880, 2000-2071, 2160-2208`
- Modify: `app.py:8718-8737`

This commit is JS + server-response-only. No tests (no Selenium/Playwright per user spec). Manual smoke check at the end.

### Task 1.1: Remove second checkbox + simplify intent row in editor

**File:** `static/js/feedback_render.js`

- [ ] **Step 1: Replace the intent-row block (currently lines 1798-1850)**

Open `static/js/feedback_render.js`. Find this block (starts with the comment "Two-checkbox intent"):

```javascript
        // Two-checkbox intent (spec 2026-05-13 §4.1) — only on feedback / improvement.
        // First box: "Amend answer key for this assignment" (always visible).
        // Second box: "Update subject standards" (hidden on legacy assignments or
        // freeform subjects since server-side enforcement drops it anyway).
        var amendCb = null;
        var promoteCb = null;
        var initialAmend = false;
        var initialPromote = false;
        if (field === 'feedback' || field === 'improvement') {
            var subjectStandardsEnabled = (
                global.ASSIGNMENT_HAS_CANONICAL_SUBJECT === true &&
                global.ASSIGNMENT_TOPIC_KEYS_STATUS !== 'legacy'
            );
            // ... rest of the block through line 1850 ...
            amendCb = makeIntentRow('Amend answer key for this assignment', initialAmend);
            amendCb.className = 'fb-cal-cb fv-amend-answer-key';
            if (subjectStandardsEnabled) {
                promoteCb = makeIntentRow('Update subject standards', initialPromote);
                promoteCb.className = 'fb-cal-cb fv-update-subject-standards';
            }
        }
```

Replace the entire block with:

```javascript
        // Single-checkbox calibration intent — only on feedback / improvement.
        // When ticked, the edit is merged into the assignment's effective
        // answer key AND triggers auto-propagation to similar submissions.
        var amendCb = null;
        var initialAmend = false;
        if (field === 'feedback' || field === 'improvement') {
            var qNow2 = state.questions[state.currentQ];
            var qKey2 = qNow2 ? String(qNow2.question_num != null ? qNow2.question_num : (state.currentQ + 1)) : null;
            var existingMeta2 = (qKey2 && state.textEditMeta && state.textEditMeta[qKey2] && state.textEditMeta[qKey2][field]) || null;
            if (existingMeta2) {
                initialAmend = !!existingMeta2.amend_answer_key;
                // Legacy single-toggle back-compat: if only old shape present, treat as amend.
                if (!('amend_answer_key' in existingMeta2) && existingMeta2.calibrated) {
                    initialAmend = true;
                }
            }

            function makeIntentRow(labelText, preChecked) {
                var wrap = document.createElement('div');
                wrap.className = 'fb-cal-wrap';
                wrap.style.cssText = 'display:flex;align-items:center;gap:6px;margin-top:6px;font-size:12px;color:#666;cursor:pointer;user-select:none;';
                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.style.cssText = 'margin:0;pointer-events:none;';
                cb.checked = !!preChecked;
                wrap.appendChild(cb);
                wrap.appendChild(document.createTextNode(labelText));
                wrap.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
                wrap.addEventListener('click', function (ev) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    cb.checked = !cb.checked;
                });
                el.appendChild(wrap);
                return cb;
            }

            amendCb = makeIntentRow('Amend answer key/rubric for this assignment', initialAmend);
            amendCb.className = 'fb-cal-cb fv-amend-answer-key';
        }
```

- [ ] **Step 2: Simplify `commit()` (currently lines 1862-1880)**

Find this block:

```javascript
        var submitted = false;
        function commit() {
            if (submitted) return;
            submitted = true;
            var newVal = textarea.value;
            var amend = !!(amendCb && amendCb.checked);
            var promote = !!(promoteCb && promoteCb.checked);
            var changed = (amend !== initialAmend) || (promote !== initialPromote);
            if (newVal === currentValue && !changed) {
                if (field === 'overall') renderShell(state);
                else renderQuestion(state);
                return;
            }
            saveTextField(state, field, newVal, amend, promote);
        }
```

Replace with:

```javascript
        var submitted = false;
        function commit() {
            if (submitted) return;
            submitted = true;
            var newVal = textarea.value;
            var amend = !!(amendCb && amendCb.checked);
            var changed = (amend !== initialAmend);
            if (newVal === currentValue && !changed) {
                if (field === 'overall') renderShell(state);
                else renderQuestion(state);
                return;
            }
            saveTextField(state, field, newVal, amend);
        }
```

- [ ] **Step 3: Simplify `saveTextField` signature and payload (lines 2000-2017)**

Find:

```javascript
    async function saveTextField(state, field, newValue, amendAnswerKey, updateSubjectStandards) {
        if (amendAnswerKey === undefined) amendAnswerKey = false;
        if (updateSubjectStandards === undefined) updateSubjectStandards = false;
        var payload;
        var savedQNum = null;
        if (field === 'overall') {
            payload = { overall_feedback: newValue };
        } else {
            var q = state.questions[state.currentQ];
            savedQNum = q.question_num != null ? q.question_num : (state.currentQ + 1);
            var qEdit = { question_num: savedQNum };
            qEdit[field] = newValue;
            if (field === 'feedback' || field === 'improvement') {
                qEdit.amend_answer_key = !!amendAnswerKey;
                qEdit.update_subject_standards = !!updateSubjectStandards;
            }
            payload = { questions: [qEdit] };
        }
```

Replace with:

```javascript
    async function saveTextField(state, field, newValue, amendAnswerKey) {
        if (amendAnswerKey === undefined) amendAnswerKey = false;
        var payload;
        var savedQNum = null;
        if (field === 'overall') {
            payload = { overall_feedback: newValue };
        } else {
            var q = state.questions[state.currentQ];
            savedQNum = q.question_num != null ? q.question_num : (state.currentQ + 1);
            var qEdit = { question_num: savedQNum };
            qEdit[field] = newValue;
            if (field === 'feedback' || field === 'improvement') {
                qEdit.amend_answer_key = !!amendAnswerKey;
            }
            payload = { questions: [qEdit] };
        }
```

- [ ] **Step 4: Simplify the `isActive` check in the save handler (around line 2032)**

Find:

```javascript
                if (fieldMeta) {
                    var isActive = !!(fieldMeta.amend_answer_key || fieldMeta.update_subject_standards);
                    // Back-compat: if the server didn't return the new flags but the old `calibrated`,
                    // fall back to that.
                    if (!('amend_answer_key' in fieldMeta) && fieldMeta.calibrated) {
                        isActive = true;
                    }
```

Replace with:

```javascript
                if (fieldMeta) {
                    var isActive = !!(fieldMeta.amend_answer_key);
                    if (!('amend_answer_key' in fieldMeta) && fieldMeta.calibrated) {
                        isActive = true;
                    }
```

- [ ] **Step 5: Simplify the indicator tag (lines 2160-2192)**

Find:

```javascript
        var isActive = !!(meta && (meta.amend_answer_key || meta.update_subject_standards || meta.calibrated));
        if (!isActive) {
            removeEditTag(state, idx, field);
            return;
        }
        // ... rest through ...
        var amend = !!(meta && meta.amend_answer_key);
        var promote = !!(meta && meta.update_subject_standards);
        if (amend && promote) {
            tag.textContent = '✓ Amended answer key and promoted to subject standards';
        } else if (promote) {
            tag.textContent = '✓ Promoted to subject standards';
        } else if (amend) {
            tag.textContent = '✓ Amended answer key for this assignment';
        } else {
            // Old-shape back-compat
            tag.textContent = '✓ Saved to calibration bank — your edit will help calibrate similar answers';
        }
```

Replace with:

```javascript
        var isActive = !!(meta && (meta.amend_answer_key || meta.calibrated));
        if (!isActive) {
            removeEditTag(state, idx, field);
            return;
        }
        // ... preserve the var prefix / qCard / fieldEl block exactly as it is ...
        var amend = !!(meta && meta.amend_answer_key);
        if (amend) {
            tag.textContent = '✓ Amended answer key for this assignment';
        } else {
            // Old-shape back-compat (pre-2026-05-13 rows)
            tag.textContent = '✓ Saved to calibration bank — your edit will help calibrate similar answers';
        }
```

Note: only the `isActive` line and the `var promote` / `if` chain change. The `var prefix`, `var qCard`, `var fieldEl`, `var rowId`, `var existing`, `var row`, `var tag` lines between them stay exactly as they are.

- [ ] **Step 6: Sanity check — no lingering references**

Run:

```bash
grep -n "update_subject_standards\|promoteCb\|initialPromote\|fv-update-subject-standards\|ASSIGNMENT_HAS_CANONICAL_SUBJECT\|ASSIGNMENT_TOPIC_KEYS_STATUS\|subjectStandardsEnabled" static/js/feedback_render.js
```

Expected: no matches. If any remain, remove them.

- [ ] **Step 7: Commit**

```bash
git add static/js/feedback_render.js
git commit -m "$(cat <<'EOF'
ui(calibration): collapse to single 'Amend answer key/rubric' checkbox

Removes the second 'Update subject standards' checkbox and all its
gating (canonical-subject + topic_keys_status checks). The JS now only
sends amend_answer_key in the PATCH payload; the indicator tag shows
'✓ Amended answer key for this assignment' or nothing.

Server-side handling for update_subject_standards is dropped in the
next commit; until then the server still accepts the old key
silently (Python ignores missing dict entries).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 1.2: Drop `update_subject_standards` from `text_edit_meta` response

**File:** `app.py:8718-8737`

- [ ] **Step 1: Replace the meta-emitting block**

Find this block (around line 8716):

```python
            if ed:
                entry['edit_id'] = ed.id
                # §4.1 two-checkbox intent: surface flags so the edit modal
                # can restore both checkboxes correctly on reopen. scope
                # values: 'individual'|'amendment'|'promoted'|'both'.
                entry['amend_answer_key'] = bool(ed.amend_answer_key)
                entry['update_subject_standards'] = ed.scope in ('promoted', 'both')
            out.setdefault(row.criterion_id, {})[row.field] = entry
```

Replace with:

```python
            if ed:
                entry['edit_id'] = ed.id
                entry['amend_answer_key'] = bool(ed.amend_answer_key)
            out.setdefault(row.criterion_id, {})[row.field] = entry
```

- [ ] **Step 2: Replace the orphan-rows block**

Find (around line 8728):

```python
        for (cid, fld), ed in active_by_key.items():
            if cid in out and fld in out[cid]:
                continue
            out.setdefault(cid, {})[fld] = {
                'version': 0,
                'calibrated': True,
                'edit_id': ed.id,
                'amend_answer_key': bool(ed.amend_answer_key),
                'update_subject_standards': ed.scope in ('promoted', 'both'),
            }
```

Replace with:

```python
        for (cid, fld), ed in active_by_key.items():
            if cid in out and fld in out[cid]:
                continue
            out.setdefault(cid, {})[fld] = {
                'version': 0,
                'calibrated': True,
                'edit_id': ed.id,
                'amend_answer_key': bool(ed.amend_answer_key),
            }
```

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
ui(calibration): stop emitting update_subject_standards in edit_meta

The JS no longer reads this field after the previous commit. Keeping
ed.scope reads here for one more commit so the read path doesn't break
before the column is dropped in commit 4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 1.3: Manual smoke check

- [ ] **Step 1: Start the dev server**

```bash
python app.py
```

Expected: server boots on port 5000, no Python tracebacks.

- [ ] **Step 2: Open a marked submission in a browser**

Navigate to any teacher-side feedback view URL (e.g. `/feedback/<submission_id>` while logged in). If the dev DB is empty, mark one submission first (or use `seed_data.py`).

- [ ] **Step 3: Verify the inline edit shows ONE checkbox**

Click any "Feedback" or "Suggested Improvement" line to open the inline editor. The single checkbox should read `Amend answer key/rubric for this assignment`. There should be no second checkbox.

- [ ] **Step 4: Verify saving without the checkbox does NOT trigger propagation**

Edit the text, leave the checkbox OFF, click outside to blur. Expected: "Saved" toast, no "Auto-applying..." toast.

- [ ] **Step 5: Verify saving WITH the checkbox triggers propagation**

Edit again on a different question, tick the checkbox, blur. Expected: "Saved" toast then "Auto-applying to N similar answer(s)…" toast (or "No other answers needed updating" if N=0).

If anything regresses, revert commits 1.1 and 1.2 with `git revert HEAD~1..HEAD` and investigate before continuing.

---

## Commit 2: Backend — field-aware + marks-aware propagation

**Files:**
- Create: `tests/test_propagation.py`
- Modify: `ai_marking.py:1887-1932`
- Modify: `app.py:6594-6611` (the `_run_propagation_worker` refresh block)
- Modify: `app.py:9061-9202` (the FeedbackEdit write block — drop promote_flag plumbing)

### Task 2.1: Write failing tests for field-routing + marks update

**File:** `tests/test_propagation.py`

- [ ] **Step 1: Create the test file with the full test set**

Create `tests/test_propagation.py` with the following contents:

```python
"""Field-aware + marks-aware propagation (spec 2026-05-16 §4.4)."""

import json
import uuid as _uuid
from unittest.mock import patch

from db import db, Teacher, Assignment, Student, Submission, FeedbackEdit


def _make_chain_two_subs(db_session):
    """Build a single Teacher / Assignment with TWO students and submissions.

    Both submissions have the same wrong answer on Q1. The second submission
    is the propagation target.
    """
    tid = 't-' + _uuid.uuid4().hex[:8]
    aid = 'a-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role='owner')
    db_session.add(t)
    asn = Assignment(
        id=aid,
        classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
        subject='biology',
        title='Test',
        teacher_id=t.id,
        provider='anthropic',
        model='claude-sonnet-4-6',
    )
    db_session.add(asn)
    db_session.commit()

    stu1 = Student(assignment_id=asn.id, index_number='1', name='Alice')
    stu2 = Student(assignment_id=asn.id, index_number='2', name='Bob')
    db_session.add_all([stu1, stu2])
    db_session.commit()

    base_q = {
        'question_num': 1,
        'student_answer': 'A wrong answer about enzymes.',
        'correct_answer': 'Enzymes are biological catalysts.',
        'feedback': 'Incorrect — does not mention catalyst function.',
        'improvement': 'Mention that enzymes are biological catalysts.',
        'marks_awarded': 0,
        'marks_total': 2,
        'status': 'incorrect',
        'feedback_source': 'original_ai',
    }
    sub1 = Submission(
        assignment_id=asn.id, student_id=stu1.id, status='done',
        result_json=json.dumps({'questions': [dict(base_q)]}),
    )
    sub2 = Submission(
        assignment_id=asn.id, student_id=stu2.id, status='done',
        result_json=json.dumps({'questions': [dict(base_q)]}),
    )
    db_session.add_all([sub1, sub2])
    db_session.commit()
    return t, asn, sub1, sub2


def _make_edit(db_session, sub, asn, teacher, field, edited_text):
    fe = FeedbackEdit(
        submission_id=sub.id,
        criterion_id='1',
        field=field,
        original_text='original',
        edited_text=edited_text,
        edited_by=teacher.id,
        assignment_id=asn.id,
        rubric_version='rv1',
        amend_answer_key=True,
        active=True,
        propagation_status='none',
    )
    db_session.add(fe)
    db_session.commit()
    return fe


def test_propagation_feedback_edit_only_rewrites_feedback(app, db_session):
    """Edit on `feedback` → only `feedback` rewritten on target. `improvement` untouched."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'feedback', 'Stricter: must say "biological catalyst".')

    fake = {'feedback': 'NEW FEEDBACK FROM HAIKU', 'marks_awarded': None}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    sub2_fresh = Submission.query.get(sub2.id)
    q = json.loads(sub2_fresh.result_json)['questions'][0]
    assert q['feedback'] == 'NEW FEEDBACK FROM HAIKU'
    # improvement must NOT have changed
    assert q['improvement'] == 'Mention that enzymes are biological catalysts.'
    assert q['feedback_source'] == 'propagated'


def test_propagation_improvement_edit_only_rewrites_improvement(app, db_session):
    """Edit on `improvement` → only `improvement` rewritten. `feedback` untouched."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'improvement', 'Be specific: name the catalyst function.')

    fake = {'improvement': 'NEW IMPROVEMENT FROM HAIKU', 'marks_awarded': None}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    sub2_fresh = Submission.query.get(sub2.id)
    q = json.loads(sub2_fresh.result_json)['questions'][0]
    assert q['improvement'] == 'NEW IMPROVEMENT FROM HAIKU'
    assert q['feedback'] == 'Incorrect — does not mention catalyst function.'
    assert q['feedback_source'] == 'propagated'


def test_propagation_lowers_marks_when_haiku_returns_lower_value(app, db_session):
    """Haiku decides marks_awarded=0 → target's marks drop."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    # Start sub2 with partial credit so we can observe a decrease.
    res2 = json.loads(sub2.result_json)
    res2['questions'][0]['marks_awarded'] = 1
    sub2.result_json = json.dumps(res2)
    db_session.commit()
    fe = _make_edit(db_session, sub1, asn, t, 'feedback', 'Stricter standard.')

    fake = {'feedback': 'F', 'marks_awarded': 0}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    q = json.loads(Submission.query.get(sub2.id).result_json)['questions'][0]
    assert q['marks_awarded'] == 0


def test_propagation_raises_marks_when_haiku_returns_higher_value(app, db_session):
    """Haiku decides marks_awarded=2 → target's marks rise."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'feedback', 'More lenient standard.')

    fake = {'feedback': 'F', 'marks_awarded': 2}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    q = json.loads(Submission.query.get(sub2.id).result_json)['questions'][0]
    assert q['marks_awarded'] == 2


def test_propagation_keeps_marks_when_haiku_returns_none(app, db_session):
    """Haiku returns marks_awarded=None → target's marks unchanged."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'feedback', 'Just clearer phrasing.')

    fake = {'feedback': 'F', 'marks_awarded': None}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    q = json.loads(Submission.query.get(sub2.id).result_json)['questions'][0]
    assert q['marks_awarded'] == 0  # unchanged from base_q


def test_propagation_passes_target_field_to_refresh_call(app, db_session):
    """Verify the worker invokes refresh_criterion_feedback with target_field=edit.field."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'improvement', 'edited')

    fake = {'improvement': 'X', 'marks_awarded': None}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake) as mock_refresh:
        _run_propagation_worker(app, fe.id, [sub2.id])

    assert mock_refresh.called, 'refresh_criterion_feedback should have been called'
    kwargs = mock_refresh.call_args.kwargs
    assert kwargs.get('target_field') == 'improvement'
```

- [ ] **Step 2: Run tests to verify they FAIL**

```bash
pytest tests/test_propagation.py -v
```

Expected: all 6 tests fail. Most likely failure mode: `TypeError: refresh_criterion_feedback() got an unexpected keyword argument 'target_field'` or assertion failures because the current worker writes to both fields.

### Task 2.2: Rewrite `refresh_criterion_feedback`

**File:** `ai_marking.py:1887-1932`

- [ ] **Step 1: Replace the function**

Find the function (starts at line 1887, ends ~line 1932):

```python
def refresh_criterion_feedback(provider, model, session_keys, subject,
                                criterion_name, student_answer, correct_answer,
                                marks_awarded, marks_total, calibration_edit):
    """Regenerate feedback + improvement for one criterion on one student,
    calibrated against a teacher's edit on another student. Text-only call —
    no images, no full marking pipeline. Cheap-tier model via HELPER_MODELS.
    Returns {feedback, improvement}.
    """
    # ... full body through `return {'feedback': feedback, 'improvement': improvement}` ...
```

Replace with:

```python
def refresh_criterion_feedback(provider, model, session_keys, subject,
                                criterion_name, student_answer, correct_answer,
                                marks_awarded, marks_total, calibration_edit,
                                target_field):
    """Regenerate one field on one criterion on one student, calibrated against
    a teacher's edit on another student. Text-only Haiku call.

    target_field is one of 'feedback' or 'improvement' — exactly the field the
    teacher edited on the source submission. Only that field is rewritten on
    the target. Haiku may also return a revised marks_awarded if the new
    calibration justifies it; if so, the caller applies it.

    Returns {target_field: str, 'marks_awarded': int|None}. marks_awarded is
    None when Haiku does not want to change marks; the caller leaves marks
    untouched in that case.
    """
    assert target_field in ('feedback', 'improvement'), (
        f"target_field must be 'feedback' or 'improvement', got {target_field!r}"
    )

    helper_model = _helper_model_for(provider, model)
    field_label = 'Feedback' if target_field == 'feedback' else 'Suggested Improvement'

    system_prompt = (
        f"You are regenerating the '{field_label}' field for one criterion on a "
        "student's script. A teacher has shown you their marking standard by "
        "editing this same field on another student's submission.\n\n"
        f"Apply the same standard to this student's answer. Rewrite ONLY the "
        f"{field_label} field. You may also revise the marks if the new "
        "calibration justifies a different score — increase, decrease, or "
        "leave unchanged. If marks should not change, return null for "
        "marks_awarded.\n\n"
        f"{FEEDBACK_GENERATION_RULES}\n\n"
        "Return JSON only:\n"
        "{\n"
        f'  "{target_field}": "...",\n'
        '  "marks_awarded": <integer or null>\n'
        "}"
    )

    orig = (calibration_edit.original_text or '')[:200]
    edited = (calibration_edit.edited_text or '')[:200]
    principle_line = ''
    cp = getattr(calibration_edit, 'correction_principle', None)
    if cp:
        principle_line = f"\nTeacher's principle: \"{cp}\""

    user_prompt = (
        f"TEACHER'S CALIBRATION EDIT (apply this standard to {field_label}):\n"
        f"Original AI {field_label}: \"{orig}\"\n"
        f"Teacher changed it to: \"{edited}\"{principle_line}\n\n"
        "NOW APPLY THE SAME STANDARD TO:\n"
        f"Subject: {subject or 'General'}\n"
        f"Criterion: {criterion_name}\n"
        f"Student's answer: {(student_answer or '')[:600]}\n"
        f"Expected answer: {(correct_answer or '')[:400]}\n"
        f"Current marks: {marks_awarded if marks_awarded is not None else '-'} "
        f"/ {marks_total if marks_total is not None else '-'}\n\n"
        "Return the JSON now."
    )

    parsed = _run_feedback_helper(provider, helper_model, session_keys,
                                   system_prompt, user_prompt, max_tokens=300)
    text = (parsed.get(target_field) or '').strip()

    new_marks = parsed.get('marks_awarded')
    if new_marks is not None:
        try:
            new_marks = int(new_marks)
            # Sanity clamp: never above marks_total, never below 0.
            if marks_total is not None and new_marks > marks_total:
                new_marks = marks_total
            if new_marks < 0:
                new_marks = 0
        except (TypeError, ValueError):
            new_marks = None

    return {target_field: text, 'marks_awarded': new_marks}
```

- [ ] **Step 2: Run propagation tests to confirm the function compiles**

```bash
pytest tests/test_propagation.py::test_propagation_passes_target_field_to_refresh_call -v
```

Expected: FAIL but with a different error than before — the function now accepts `target_field`, so the failure should be a `TypeError` from the worker still calling without it, or an assertion mismatch. We'll fix the worker next.

### Task 2.3: Update `_run_propagation_worker` to be field-aware

**File:** `app.py:6594-6611`

- [ ] **Step 1: Replace the refresh + result-write block**

Find this block (around line 6594):

```python
                    refreshed = refresh_criterion_feedback(
                        provider=asn.provider,
                        model=asn.model,
                        session_keys=_resolve_api_keys(asn),
                        subject=asn.subject or '',
                        criterion_name=edit.criterion_id,
                        student_answer=target_q.get('student_answer') or '',
                        correct_answer=target_q.get('correct_answer') or '',
                        marks_awarded=target_q.get('marks_awarded'),
                        marks_total=target_q.get('marks_total'),
                        calibration_edit=edit,
                    )
                    target_q['feedback'] = refreshed['feedback'] or target_q.get('feedback') or ''
                    target_q['improvement'] = refreshed['improvement'] or target_q.get('improvement') or ''
                    target_q['feedback_source'] = 'propagated'
                    target_q['propagated_from_edit'] = edit.id
                    sub.set_result(result)
                    db.session.commit()
```

Replace with:

```python
                    # Field-aware re-mark: pass the teacher's edited field
                    # through; refresh_criterion_feedback returns only that
                    # field + an optional marks_awarded override.
                    target_field = edit.field
                    if target_field not in ('feedback', 'improvement'):
                        # Defensive: server-side validation should already
                        # prevent this, but if it slipped through, skip the
                        # candidate with a clear error rather than corrupting
                        # the result.
                        entry = {'submission_id': int(sid), 'status': 'failed',
                                 'error': f'unsupported field for propagation: {target_field!r}'}
                        results.append(entry)
                        continue

                    refreshed = refresh_criterion_feedback(
                        provider=asn.provider,
                        model=asn.model,
                        session_keys=_resolve_api_keys(asn),
                        subject=asn.subject or '',
                        criterion_name=edit.criterion_id,
                        student_answer=target_q.get('student_answer') or '',
                        correct_answer=target_q.get('correct_answer') or '',
                        marks_awarded=target_q.get('marks_awarded'),
                        marks_total=target_q.get('marks_total'),
                        calibration_edit=edit,
                        target_field=target_field,
                    )
                    new_text = (refreshed.get(target_field) or target_q.get(target_field) or '')
                    target_q[target_field] = new_text
                    new_marks = refreshed.get('marks_awarded')
                    if new_marks is not None:
                        target_q['marks_awarded'] = new_marks
                        # Re-derive status to stay consistent with marks.
                        mt = target_q.get('marks_total')
                        if mt and mt > 0:
                            ratio = (new_marks or 0) / mt
                            if ratio >= 0.99:
                                target_q['status'] = 'correct'
                            elif ratio > 0:
                                target_q['status'] = 'partially_correct'
                            else:
                                target_q['status'] = 'incorrect'
                    target_q['feedback_source'] = 'propagated'
                    target_q['propagated_from_edit'] = edit.id
                    sub.set_result(result)
                    db.session.commit()
```

- [ ] **Step 2: Run all propagation tests**

```bash
pytest tests/test_propagation.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 3: Run the full pre-existing test suite to verify nothing else broke**

```bash
pytest tests/ -v --ignore=tests/test_subject_standards.py --ignore=tests/test_subject_topic_vocab.py 2>&1 | tail -30
```

Expected: green except for any test that imports the soon-to-be-deleted modules. If `test_calibration_intent.py` fails because of the still-present-but-changing intent code, that's expected — note which tests fail; we refactor them in commit 3.

### Task 2.4: Drop `promote_flag` and `scope='promoted'/'both'` from POST /result

**File:** `app.py:9061-9255`

- [ ] **Step 1: Replace the intent-flag block**

Find lines 9068-9080:

```python
            # Two-checkbox intent (spec 2026-05-13 §4.1).
            amend_flag = bool(edit.get('amend_answer_key'))
            promote_flag = bool(edit.get('update_subject_standards'))
            # Server-side suppression: legacy assignments hide 'Update subject
            # standards'; freeform subjects can't promote; demo mode disables.
            if promote_flag:
                from subjects import resolve_subject_key as _resolve_subj
                if (asn.topic_keys_status == 'legacy'
                        or _resolve_subj(asn.subject or '') is None
                        or os.environ.get('DEMO_MODE', 'FALSE').upper() == 'TRUE'):
                    promote_flag = False
            cal_flag = amend_flag or promote_flag
```

Replace with:

```python
            # Single-intent calibration (spec 2026-05-16): only amend_answer_key.
            amend_flag = bool(edit.get('amend_answer_key'))
            cal_flag = amend_flag
```

- [ ] **Step 2: Replace the uncheck-path block**

Find lines 9116-9127:

```python
                        # Uncheck path: deactivate any prior bank row.
                        if not cal_flag:
                            if prior:
                                prior.active = False
                                db.session.flush()
                            entry = {'amend_answer_key': False,
                                     'update_subject_standards': False,
                                     'calibrated': False}
                            if log_meta and log_meta.get('version'):
                                entry['version'] = log_meta['version']
                            edit_meta.setdefault(str(qn), {})[_field] = entry
                            sp.commit()
                            continue
```

Replace with:

```python
                        # Uncheck path: deactivate any prior bank row.
                        if not cal_flag:
                            if prior:
                                prior.active = False
                                db.session.flush()
                            entry = {'amend_answer_key': False,
                                     'calibrated': False}
                            if log_meta and log_meta.get('version'):
                                entry['version'] = log_meta['version']
                            edit_meta.setdefault(str(qn), {})[_field] = entry
                            sp.commit()
                            continue
```

- [ ] **Step 3: Replace the idempotent re-affirm block**

Find lines 9129-9151:

```python
                        # Idempotent re-affirm: text unchanged AND prior text
                        # matches AND the intent flags also match the prior
                        # row. If the teacher unchecked a box but kept the
                        # text the same, that's a flag change — not an
                        # idempotent re-affirm — so we must fall through to
                        # the "write new row" path below, which deactivates
                        # the prior row and persists the new flag state.
                        prior_amend = bool(prior.amend_answer_key) if prior else False
                        prior_promote = (prior.scope in ('promoted', 'both')) if prior else False
                        flags_match = (prior_amend == amend_flag and prior_promote == promote_flag)
                        if (new_text == old_text and prior
                                and (prior.edited_text or '') == new_text
                                and flags_match):
                            entry = {
                                'edit_id': prior.id,
                                'amend_answer_key': prior_amend,
                                'update_subject_standards': prior_promote,
                                'promoted_standard_id': None,
                                'calibrated': True,  # back-compat
                            }
                            edit_meta.setdefault(str(qn), {})[_field] = entry
                            sp.rollback()
                            continue
```

Replace with:

```python
                        # Idempotent re-affirm: text unchanged AND prior text
                        # matches AND amend_answer_key flag matches the prior
                        # row. Otherwise fall through to the "write new row"
                        # path which deactivates the prior row.
                        prior_amend = bool(prior.amend_answer_key) if prior else False
                        if (new_text == old_text and prior
                                and (prior.edited_text or '') == new_text
                                and prior_amend == amend_flag):
                            entry = {
                                'edit_id': prior.id,
                                'amend_answer_key': prior_amend,
                                'calibrated': True,  # back-compat
                            }
                            edit_meta.setdefault(str(qn), {})[_field] = entry
                            sp.rollback()
                            continue
```

- [ ] **Step 4: Replace the FeedbackEdit construction block**

Find lines 9153-9202:

```python
                        # Write a new FeedbackEdit row. Anchor original_text
                        # to the AI original (prior row's original_text if it
                        # exists; pre-edit text otherwise).
                        original_text = (prior.original_text if prior else old_text) or old_text
                        if prior:
                            prior.active = False
                        _scope = ('both' if (amend_flag and promote_flag)
                                  else 'promoted' if promote_flag
                                  else 'amendment')
                        new_edit = FeedbackEdit(
                            submission_id=sub.id,
                            criterion_id=str(qn),
                            field=_field,
                            original_text=original_text,
                            edited_text=new_text,
                            edited_by=editor_id,
                            assignment_id=asn.id,
                            rubric_version=rubric_hash,
                            mistake_type=target.get('mistake_type'),
                            scope=_scope,
                            amend_answer_key=amend_flag,
                            active=True,
                            propagation_status='none',
                        )
                        db.session.add(new_edit)
                        db.session.flush()
                        sp.commit()
                        fresh_calibration_edits.append(new_edit)
                        if promote_flag:
                            promotion_pending.append((new_edit, str(qn), _field))
                        entry = {
                            'edit_id': new_edit.id,
                            'amend_answer_key': amend_flag,
                            'update_subject_standards': promote_flag,
                            'promoted_standard_id': None,  # filled in post-loop
                            'calibrated': True,  # back-compat with old JS
                        }
                        if log_meta and log_meta.get('version'):
                            entry['version'] = log_meta['version']
                        edit_meta.setdefault(str(qn), {})[_field] = entry
                    except Exception:
```

Replace with:

```python
                        # Write a new FeedbackEdit row. Anchor original_text
                        # to the AI original (prior row's original_text if it
                        # exists; pre-edit text otherwise).
                        original_text = (prior.original_text if prior else old_text) or old_text
                        if prior:
                            prior.active = False
                        new_edit = FeedbackEdit(
                            submission_id=sub.id,
                            criterion_id=str(qn),
                            field=_field,
                            original_text=original_text,
                            edited_text=new_text,
                            edited_by=editor_id,
                            assignment_id=asn.id,
                            rubric_version=rubric_hash,
                            mistake_type=target.get('mistake_type'),
                            scope='amendment',  # column dropped in commit 4
                            amend_answer_key=amend_flag,
                            active=True,
                            propagation_status='none',
                        )
                        db.session.add(new_edit)
                        db.session.flush()
                        sp.commit()
                        fresh_calibration_edits.append(new_edit)
                        entry = {
                            'edit_id': new_edit.id,
                            'amend_answer_key': amend_flag,
                            'calibrated': True,  # back-compat with old JS
                        }
                        if log_meta and log_meta.get('version'):
                            entry['version'] = log_meta['version']
                        edit_meta.setdefault(str(qn), {})[_field] = entry
                    except Exception:
```

- [ ] **Step 5: Remove the `promotion_pending` post-loop block**

Find this block (around line 9236-9255):

```python
    # Promote flagged edits AFTER the main commit so FeedbackEdit.id exists durably.
    if promotion_pending:
        try:
            from subject_standards import promote_to_subject_standard
            session_keys_for_promote = asn.get_api_keys() or {}
            for fe_row, qn_str, field_str in promotion_pending:
                try:
                    ss_id = promote_to_subject_standard(
                        feedback_edit_id=fe_row.id,
                        provider=asn.provider,
                        model=asn.model,
                        session_keys=session_keys_for_promote,
                    )
                    em = edit_meta.setdefault(qn_str, {}).setdefault(field_str, {})
                    em['promoted_standard_id'] = ss_id
                except Exception as promote_err:
                    logger.warning(
                        f'promote_to_subject_standard failed for edit {fe_row.id}: {promote_err}'
                    )
        except Exception as outer_promote_err:
            logger.exception(f'promotion batch failed: {outer_promote_err}')
```

Delete the entire block. Then search for and delete any `promotion_pending = []` declaration earlier in this function (likely near the top of the route handler where `fresh_calibration_edits = []` is initialized).

- [ ] **Step 6: Run the propagation + amendment tests**

```bash
pytest tests/test_propagation.py tests/test_bank_amendment_push.py -v
```

Expected: green. `test_bank_amendment_push.py` exercises the amend flow specifically.

- [ ] **Step 7: Run full suite, ignoring deletable test files**

```bash
pytest tests/ -v --ignore=tests/test_subject_standards.py --ignore=tests/test_subject_topic_vocab.py 2>&1 | tail -30
```

Expected: tests that rely on `update_subject_standards` flag in payloads will fail in `test_calibration_intent.py`. Note them. We refactor in commit 3.

- [ ] **Step 8: Commit**

```bash
git add tests/test_propagation.py ai_marking.py app.py
git commit -m "$(cat <<'EOF'
feat(propagation): field-aware re-mark + marks_awarded updates

refresh_criterion_feedback now takes target_field ('feedback' or
'improvement') and returns only that field plus an optional
marks_awarded override. The propagation worker routes the result to
the correct key on the target submission's result_json and updates
marks (with status recompute) when Haiku returns a new value.

Also drops the server-side promote_flag plumbing in POST /result and
the post-commit promote_to_subject_standard batch — the JS stopped
sending update_subject_standards in commit 1.

Tests: 6 new tests in tests/test_propagation.py covering field
routing both directions, marks up/down/unchanged, and the
target_field call argument.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Commit 3: Backend deletions — topic tagging, subject standards, dead helpers

This is bulk deletion. Pattern per file: delete or shrink → run remaining test suite → verify green → continue.

### Task 3.1: Delete subject-standards-only tests

**Files:**
- Delete: `tests/test_subject_standards.py`
- Delete: `tests/test_subject_topic_vocab.py`

- [ ] **Step 1: Delete the test files**

```bash
git rm tests/test_subject_standards.py tests/test_subject_topic_vocab.py
```

- [ ] **Step 2: Refactor `tests/test_calibration_intent.py`**

Open `tests/test_calibration_intent.py` and:
- Delete every test function whose name contains `promote`, `subject_standard`, or `update_subject_standards` (search for those substrings).
- In every remaining test, delete any setup line that sets `scope='promoted'` or `scope='both'`, and any assertion that checks `update_subject_standards`.
- Keep tests that exercise `amend_answer_key=True` or the uncheck/retire path.
- Where a test still constructs `FeedbackEdit(...)`, leave `scope='amendment'` (the column still exists; commit 4 drops it and updates this test again).

Run:

```bash
grep -n "promote\|subject_standard\|update_subject_standards\|scope='promoted'\|scope='both'" tests/test_calibration_intent.py
```

Expected: no matches.

- [ ] **Step 3: Run the surviving calibration test suite**

```bash
pytest tests/test_calibration_intent.py -v
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add -A tests/
git commit -m "$(cat <<'EOF'
test: drop subject-standards tests, refactor calibration_intent to amend-only

Deletes tests/test_subject_standards.py (727 lines) and
tests/test_subject_topic_vocab.py wholesale — the features they exercise
are being removed in this commit and commit 4.

tests/test_calibration_intent.py: drop every test referencing the
'update_subject_standards' intent; keep tests exercising amend-only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3.2: Delete templates and config

- [ ] **Step 1: Delete the subject standards template**

```bash
git rm templates/subject_standards.html
```

- [ ] **Step 2: Delete the per-subject topic vocab configs**

```bash
git rm -r config/subject_topics/
```

This removes 9 files: `__init__.py`, `biology.py`, `chemistry.py`, `english.py`, `geography.py`, `history.py`, `lower_secondary_science.py`, `mathematics.py`, `physics.py`.

- [ ] **Step 3: Verify no source file still imports from `config.subject_topics`**

```bash
grep -rn "config.subject_topics\|from config import subject_topics" --include="*.py" .
```

Expected: only matches in `subject_standards.py` and possibly `ai_marking.py` — we delete those imports in the next tasks. If matches appear in tests, those tests should already have been deleted in Task 3.1.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore: delete subject-standards template + topic vocab config

templates/subject_standards.html — HOD review queue page (no longer
reachable after route deletion).

config/subject_topics/ — 9 files of per-subject controlled vocabulary,
used only by the topic-tagging pipeline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3.3: Shrink `subject_standards.py` to just `build_effective_answer_key`

**File:** `subject_standards.py`

- [ ] **Step 1: Replace the entire file**

Overwrite `subject_standards.py` with this content:

```python
"""Per-assignment answer-key amendments from FeedbackEdit rows.

This module shrank dramatically in 2026-05-16: topic tagging, subject
standards retrieval, promotion, and dedup were all removed. The sole
remaining responsibility is assembling 'Teacher clarifications' from
active amend_answer_key=True edits into the marking prompt's effective
answer key.
"""
from db import FeedbackEdit


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

- [ ] **Step 2: Verify no caller relies on the deleted functions**

```bash
grep -rn "promote_to_subject_standard\|find_similar_standard\|retrieve_subject_standards\|find_related_standards\|seed_subject_topic_vocabulary\|_text_similarity" --include="*.py" .
```

Expected: no matches outside `app.py`. Any matches in `app.py` will be removed in Task 3.5.

### Task 3.4: Delete topic-extraction AI calls from `ai_marking.py`

**File:** `ai_marking.py`

- [ ] **Step 1: Delete `extract_assignment_topic_keys` (lines 2451-2523)**

Find the function `def extract_assignment_topic_keys(provider, model, session_keys, subject, questions, max_retries=3):` and delete the entire function definition through its closing `return` statement.

- [ ] **Step 2: Delete `extract_assignment_topic_keys_from_pdf` (lines 2526-2623)**

Find `def extract_assignment_topic_keys_from_pdf(` and delete the entire function.

- [ ] **Step 3: Delete `extract_standard_topic_keys` (lines 2627-2668)**

Find `def extract_standard_topic_keys(` and delete the entire function.

- [ ] **Step 4: Find and update `build_calibration_block`**

Search for `def build_calibration_block(` (around line 2701). Per the spec, this is already a no-op stub. Read its body — if it's already returning `''`, leave it. If it has any residual logic referencing the deleted functions, replace the body with:

```python
def build_calibration_block(teacher_id, asn, subject, mistake_types,
                            provider, model, session_keys):
    """Deprecated no-op kept for back-compat with any straggling caller.
    The real calibration block is now assembled by
    app._build_calibration_block_for from amend_answer_key FeedbackEdits.
    """
    return ''
```

- [ ] **Step 5: Verify imports still resolve**

```bash
python -c "import ai_marking; print('ok')"
```

Expected: `ok`.

### Task 3.5: Delete topic-tagging + subject-standards routes from `app.py`

**File:** `app.py`

- [ ] **Step 1: Delete `_kick_off_topic_tagging` (lines 1167-1231)**

Find `def _kick_off_topic_tagging(asn):` and delete the entire function.

- [ ] **Step 2: Delete `_can_edit_subject_standards` (lines 851-862)**

Find `def _can_edit_subject_standards(teacher, subject=None):` and delete the entire function.

- [ ] **Step 3: Delete `_normalize_topic_keys`**

Search for `def _normalize_topic_keys(`. Delete the entire function.

- [ ] **Step 4: Shrink `_build_calibration_block_for` (lines 1235-end of function)**

Find `def _build_calibration_block_for(asn, sub=None):` and replace the entire function body (everything from the docstring through the final `return`) with:

```python
def _build_calibration_block_for(asn, sub=None):
    """Assemble the marking-prompt calibration block from active
    amend_answer_key FeedbackEdits on this assignment. Returns '' if
    there are no amendments. Never raises — exceptions are swallowed
    and a warning logged; marking is never blocked.
    """
    try:
        from subject_standards import build_effective_answer_key
        merged = build_effective_answer_key(asn, '')
        marker = '── Teacher clarifications'
        if marker in merged:
            idx = merged.find(marker)
            amendments_text = merged[idx:].rstrip()
            if amendments_text:
                logger.info(
                    f"Calibration block resolved for asn={getattr(asn,'id',None)}: "
                    f"{len(amendments_text)} chars"
                )
                return amendments_text
        return ''
    except Exception as e:
        logger.warning(f"_build_calibration_block_for failed: {e}")
        return ''
```

The function's two callers (`app.py:5614` and `app.py:6223`) keep their signatures and call sites — no other changes needed.

- [ ] **Step 5: Delete subject-standards routes (lines 7623-7792)**

Find the first `@app.route('/teacher/subject-standards')` decorator and delete every route block through the last `api_subject_standards_export()` function. That's 8 functions:

- `teacher_subject_standards_page` (route `/teacher/subject-standards`)
- `api_subject_standards_list` (route `/api/subject_standards`)
- `_load_standard_or_404` (helper)
- `api_subject_standards_approve` (`/api/subject_standards/<id>/approve`)
- `api_subject_standards_edit` (`/api/subject_standards/<id>/edit`)
- `api_subject_standards_reject` (`/api/subject_standards/<id>/reject`)
- `api_subject_standards_related` (`/api/subject_standards/<id>/related`)
- `api_subject_standards_export` (`/api/subject_standards/export`)

Delete all 8 in one contiguous block.

- [ ] **Step 6: Delete the topic-tagging extract route**

Search for `@app.route('/api/assignment/<` followed by `extract-topics` or `extract_topics`. Delete that route function entirely.

- [ ] **Step 7: Remove any remaining references to `topic_keys_status` or `topic_keys`**

```bash
grep -n "topic_keys_status\|topic_keys" app.py
```

Expected after Step 4: no matches in `_build_calibration_block_for`. If matches exist elsewhere, inspect each one — they're likely filters in listing routes (e.g. `app.py:9075`). Each should be deletable since the column is going in commit 4. Where the filter excluded `topic_keys_status == 'legacy'`, simply remove that condition.

- [ ] **Step 8: Run app boot smoke test**

```bash
python -c "from app import app; print('ok')"
```

Expected: `ok` with no ImportError or NameError.

```bash
python app.py &
SERVER_PID=$!
sleep 3
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/
kill $SERVER_PID
```

Expected: `200` or `302` (depending on auth state). If `500` — check the server log; something in the deletion missed a caller.

### Task 3.6: Delete model classes from `db.py`

**File:** `db.py`

- [ ] **Step 1: Delete `class SubjectStandard` (lines 1614-1645)**

Find `class SubjectStandard(db.Model):` and delete the entire class definition.

- [ ] **Step 2: Delete `class SubjectTopicVocabulary` (lines 1648-1656)**

Find `class SubjectTopicVocabulary(db.Model):` and delete the entire class.

- [ ] **Step 3: Remove topic-tagging classification from `_migrate_calibration_runtime` (lines 808-820)**

Find this block:

```python
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
```

Replace with:

```python
        # (Topic-tagging assignment classification removed 2026-05-16.
        # The column itself is dropped in _migrate_drop_subject_standards.)
```

Then find the `for fe in FeedbackEdit.query.filter_by(active=True).all():` block immediately after:

```python
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
```

Replace with:

```python
        # FeedbackEdit calibration intent backfill: anything still active
        # with rubric_version set is treated as an amendment. Legacy
        # promoted-only rows are deactivated in
        # _migrate_drop_subject_standards (commit 4).
        for fe in FeedbackEdit.query.filter_by(active=True).all():
            parent = Assignment.query.get(fe.assignment_id)
            if parent is None:
                fe.active = False
                continue
            fe.amend_answer_key = True
            fe.scope = 'amendment'
```

- [ ] **Step 4: Remove `seed_subject_topic_vocabulary` call from `init_db`**

Find the call to `seed_subject_topic_vocabulary()` in `init_db` (search for it). Delete that line.

- [ ] **Step 5: Confirm no stale imports of deleted classes**

```bash
grep -n "SubjectStandard\|SubjectTopicVocabulary" db.py
```

Expected: no matches.

- [ ] **Step 6: Verify the app still boots**

```bash
python -c "from db import db, Assignment, FeedbackEdit, Submission; print('ok')"
python -c "from app import app; print('ok')"
```

Expected: both `ok`.

### Task 3.7: Update CLAUDE.md

**File:** `CLAUDE.md`

- [ ] **Step 1: Replace the "Calibration system (subject standards)" section**

Find the section heading `### Calibration system (subject standards)`. Replace the entire section (heading through the next `### ` heading or `##` heading) with:

```markdown
### Calibration system (amend answer key)

Per-assignment calibration only. When a teacher edits a feedback line on a marked submission and ticks "Amend answer key/rubric for this assignment":

- A `FeedbackEdit` row is written with `amend_answer_key=True`.
- The edit is merged into the marking prompt's effective answer key for that assignment via `subject_standards.build_effective_answer_key`.
- A background propagation worker runs the same correction across other submissions on the same assignment whose criterion lost marks. The Haiku re-mark is field-aware (rewrites only the field the teacher edited: `feedback` or `improvement`) and may update `marks_awarded` if the new standard justifies a different score.

Cross-assignment, cross-teacher, and cross-class learning are intentionally NOT supported — each assignment is calibrated only by edits on its own submissions.

Spec: `docs/superpowers/specs/2026-05-16-calibration-simplification-design.md`.
```

- [ ] **Step 2: Remove the load-bearing-fields entry for topic keys**

In CLAUDE.md, find the "Currently load-bearing fields" list. Remove any reference to `topic_keys_status`. The `Assignment.subject` and `FeedbackEdit.mistake_type` entries stay.

- [ ] **Step 3: Commit the entire backend deletion**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: remove subject standards + topic tagging backend

Deletes:
- subject_standards.py shrunk to just build_effective_answer_key
- ai_marking.py: extract_assignment_topic_keys{,_from_pdf},
  extract_standard_topic_keys
- app.py: 8 /api/subject_standards/* routes, /teacher/subject-standards
  page, /api/assignment/<id>/extract-topics route,
  _kick_off_topic_tagging, _can_edit_subject_standards,
  _normalize_topic_keys
- app.py: _build_calibration_block_for shrunk to just teacher
  clarifications (no more standards retrieval block)
- db.py: SubjectStandard + SubjectTopicVocabulary model classes,
  topic_keys_status classification in _migrate_calibration_runtime,
  seed_subject_topic_vocabulary call from init_db

Updates CLAUDE.md to describe the simplified single-checkbox flow.

Schema migration (DROP TABLE + DROP COLUMN) is the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Run the full test suite + boot smoke**

```bash
pytest tests/ -v 2>&1 | tail -30
python -c "from app import app; print('boot ok')"
```

Expected: green; "boot ok".

---

## Commit 4: Schema migration — DROP TABLE + DROP COLUMN

**Files:**
- Modify: `db.py` — add `_drop_columns` helper + `_migrate_drop_subject_standards`; wire into `init_db`
- Modify: `tests/test_migration_calibration.py` — add cases for the new migration

### Task 4.1: Write failing migration test

**File:** `tests/test_migration_calibration.py`

- [ ] **Step 1: Append new test cases**

Open `tests/test_migration_calibration.py` and append at the end:

```python


# --- 2026-05-16: drop_subject_standards migration -------------------------


def test_drop_migration_removes_subject_standards_table(app, db_session):
    """After migration, subject_standards table is absent."""
    from db import _migrate_drop_subject_standards
    db.engine.execute(
        "CREATE TABLE IF NOT EXISTS subject_standards (id INTEGER PRIMARY KEY)"
    )
    _migrate_drop_subject_standards(app, force=True)
    rows = db.engine.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subject_standards'"
    ).fetchall()
    assert rows == [], 'subject_standards table should be dropped'


def test_drop_migration_removes_subject_topic_vocabulary_table(app, db_session):
    """After migration, subject_topic_vocabulary table is absent."""
    from db import _migrate_drop_subject_standards
    db.engine.execute(
        "CREATE TABLE IF NOT EXISTS subject_topic_vocabulary (subject TEXT, topic_key TEXT)"
    )
    _migrate_drop_subject_standards(app, force=True)
    rows = db.engine.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subject_topic_vocabulary'"
    ).fetchall()
    assert rows == [], 'subject_topic_vocabulary table should be dropped'


def test_drop_migration_removes_feedback_edits_dead_columns(app, db_session):
    """After migration, the obsolete FeedbackEdit columns are gone."""
    from db import _migrate_drop_subject_standards
    _migrate_drop_subject_standards(app, force=True)
    cols = [
        r[1] for r in db.engine.execute("PRAGMA table_info(feedback_edits)").fetchall()
    ]
    for dead in ('scope', 'promoted_to_subject_standard_id',
                 'promoted_by', 'promoted_at'):
        assert dead not in cols, f'feedback_edits.{dead} should be dropped'


def test_drop_migration_removes_assignments_dead_columns(app, db_session):
    """After migration, the obsolete Assignment columns are gone."""
    from db import _migrate_drop_subject_standards
    _migrate_drop_subject_standards(app, force=True)
    cols = [
        r[1] for r in db.engine.execute("PRAGMA table_info(assignments)").fetchall()
    ]
    for dead in ('topic_keys', 'topic_keys_status'):
        assert dead not in cols, f'assignments.{dead} should be dropped'


def test_drop_migration_is_idempotent(app, db_session):
    """Running the migration twice is safe — second call no-ops."""
    from db import _migrate_drop_subject_standards
    _migrate_drop_subject_standards(app, force=True)
    _migrate_drop_subject_standards(app)
    rows = db.engine.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('subject_standards', 'subject_topic_vocabulary')"
    ).fetchall()
    assert rows == []
```

- [ ] **Step 2: Run to verify they FAIL**

```bash
pytest tests/test_migration_calibration.py -v -k drop_migration
```

Expected: 5 failures with `ImportError: cannot import name '_migrate_drop_subject_standards' from 'db'`.

### Task 4.2: Implement `_drop_columns` helper

**File:** `db.py`

- [ ] **Step 1: Add `_drop_columns` helper next to `_migrate_add_columns`**

Find `def _migrate_add_columns(app):` (around line 67). Above or below it, add the new helper:

```python
def _drop_columns(table_name, columns_to_drop):
    """Drop columns from a table on both SQLite and Postgres.

    SQLite (pre-3.35) had no DROP COLUMN, so we use the table-rebuild
    dance. The rebuild dance is portable and lets us drop multiple
    columns in one pass.

    Postgres supports native ALTER TABLE ... DROP COLUMN, which is much
    cheaper than copying the table on Postgres-sized data.

    Idempotent: silently ignores columns that don't exist.
    """
    dialect = db.engine.dialect.name

    if dialect == 'postgresql':
        existing = set()
        rows = db.engine.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t",
            {'t': table_name}
        ).fetchall()
        for r in rows:
            existing.add(r[0])

        for col in columns_to_drop:
            if col not in existing:
                continue
            db.engine.execute(
                f'ALTER TABLE {table_name} DROP COLUMN {col}'
            )
        return

    # SQLite path: table rebuild.
    info = db.engine.execute(f'PRAGMA table_info({table_name})').fetchall()
    if not info:
        return  # table doesn't exist
    existing_cols = [r[1] for r in info]  # (cid, name, type, notnull, dflt, pk)
    to_drop = [c for c in columns_to_drop if c in existing_cols]
    if not to_drop:
        return  # idempotent no-op

    kept_cols = [c for c in existing_cols if c not in to_drop]
    kept_cols_csv = ', '.join(kept_cols)

    new_table = f'__new__{table_name}'
    db.engine.execute(f'DROP TABLE IF EXISTS {new_table}')

    col_defs = []
    pk_cols = []
    for r in info:
        cid, name, ctype, notnull, dflt, pk = r
        if name in to_drop:
            continue
        line = f'{name} {ctype}'
        if notnull:
            line += ' NOT NULL'
        if dflt is not None:
            line += f' DEFAULT {dflt}'
        if pk:
            pk_cols.append(name)
        col_defs.append(line)
    if pk_cols:
        col_defs.append(f'PRIMARY KEY ({", ".join(pk_cols)})')

    db.engine.execute(
        f'CREATE TABLE {new_table} ({", ".join(col_defs)})'
    )
    db.engine.execute(
        f'INSERT INTO {new_table} ({kept_cols_csv}) '
        f'SELECT {kept_cols_csv} FROM {table_name}'
    )
    db.engine.execute(f'DROP TABLE {table_name}')
    db.engine.execute(f'ALTER TABLE {new_table} RENAME TO {table_name}')

    # Recreate indexes that existed on the old table.
    indexes = db.engine.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:t "
        "AND sql IS NOT NULL",
        {'t': table_name}
    ).fetchall()
    for (sql,) in indexes:
        try:
            db.engine.execute(sql)
        except Exception:
            pass  # index already created during table rebuild
```

### Task 4.3: Implement `_migrate_drop_subject_standards`

**File:** `db.py`

- [ ] **Step 1: Add the migration name constant and function**

Below `_drop_columns`, add:

```python
_DROP_SUBJECT_STANDARDS_MIGRATION_NAME = 'drop_subject_standards_2026_05_16'


def _migrate_drop_subject_standards(_app, force=False):
    """Drop subject_standards + subject_topic_vocabulary tables and the
    obsolete FeedbackEdit + Assignment columns. Idempotent via
    MigrationFlag. force=True bypasses idempotency (tests only).

    Uses raw SQL throughout: by this commit, the ORM no longer maps the
    affected columns, so SQLAlchemy queries would fail at parse time.
    """
    with _app.app_context():
        marker = MigrationFlag.query.filter_by(
            name=_DROP_SUBJECT_STANDARDS_MIGRATION_NAME
        ).first()
        if marker is not None and not force:
            logger.debug(
                'drop_subject_standards migration already applied at %s',
                marker.applied_at,
            )
            return
        if force:
            logger.info('drop_subject_standards: forced re-run (tests only)')
        else:
            logger.info('drop_subject_standards: first run on this DB')

        # 1. Deactivate legacy promoted-only FeedbackEdits.
        try:
            db.engine.execute(
                "UPDATE feedback_edits "
                "SET active = 0 "
                "WHERE scope = 'promoted' AND amend_answer_key = 0 AND active = 1"
            )
        except Exception as e:
            logger.warning(f'legacy-promoted deactivation skipped: {e}')

        # 2. Drop tables.
        try:
            db.engine.execute('DROP TABLE IF EXISTS subject_standards')
        except Exception as e:
            logger.warning(f'DROP TABLE subject_standards skipped: {e}')
        try:
            db.engine.execute('DROP TABLE IF EXISTS subject_topic_vocabulary')
        except Exception as e:
            logger.warning(f'DROP TABLE subject_topic_vocabulary skipped: {e}')

        # 3. Drop columns.
        _drop_columns('feedback_edits', [
            'scope', 'promoted_to_subject_standard_id',
            'promoted_by', 'promoted_at',
        ])
        _drop_columns('assignments', ['topic_keys', 'topic_keys_status'])

        if marker is None:
            db.session.add(MigrationFlag(name=_DROP_SUBJECT_STANDARDS_MIGRATION_NAME))
            db.session.commit()
```

- [ ] **Step 2: Wire into `init_db`**

Find `init_db` (around line 1044) where `_migrate_add_columns(app)` is called. After the existing migrations (after `_migrate_calibration_runtime` and `_migrate_result_json_theme_to_mistake_type` calls), add:

```python
            _migrate_drop_subject_standards(app)
```

### Task 4.4: Remove the ORM `scope`, `promoted_to_subject_standard_id`, `promoted_by`, `promoted_at`, `topic_keys`, `topic_keys_status` fields

**File:** `db.py`

- [ ] **Step 1: Find the `FeedbackEdit` class and remove obsolete columns**

In the `class FeedbackEdit(db.Model):` definition (around line 1571), delete these column definitions:

- `scope = db.Column(...)`
- `promoted_by = db.Column(...)`
- `promoted_at = db.Column(...)`
- `promoted_to_subject_standard_id = db.Column(...)`

- [ ] **Step 2: Find the `Assignment` class and remove topic_keys columns**

In `class Assignment(db.Model):` definition, delete:

- `topic_keys = db.Column(...)`
- `topic_keys_status = db.Column(...)`

- [ ] **Step 3: Remove the `scope='amendment'` argument from the FeedbackEdit construction in `app.py`**

Find the `new_edit = FeedbackEdit(...)` construction modified in commit 2 (around line 9162). Delete the line `scope='amendment',  # column dropped in commit 4`.

- [ ] **Step 4: Remove the `fe.scope = 'amendment'` line from `_migrate_calibration_runtime`**

Find this block in `_migrate_calibration_runtime` (it was modified in commit 3 Task 3.6 Step 3):

```python
        for fe in FeedbackEdit.query.filter_by(active=True).all():
            parent = Assignment.query.get(fe.assignment_id)
            if parent is None:
                fe.active = False
                continue
            fe.amend_answer_key = True
            fe.scope = 'amendment'
```

Replace with:

```python
        for fe in FeedbackEdit.query.filter_by(active=True).all():
            parent = Assignment.query.get(fe.assignment_id)
            if parent is None:
                fe.active = False
                continue
            fe.amend_answer_key = True
```

(`fe.scope = 'amendment'` line removed because the ORM no longer maps `scope`.)

- [ ] **Step 5: Drop remaining `scope=` kwargs from test files**

```bash
grep -rn "scope=" tests/ --include="*.py"
```

For each match: if it's a FeedbackEdit kwarg, delete the kwarg. The ORM no longer accepts it.

- [ ] **Step 6: Verify migration order is correct**

Read `init_db` (around line 1042-1050). Verify the call order is:
1. `_migrate_add_columns(app)` (or equivalent)
2. `_migrate_calibration_runtime(app)`
3. `_migrate_result_json_theme_to_mistake_type(app)`
4. `_migrate_drop_subject_standards(app)` ← new, must be LAST

If the order is wrong, reorder them. The drop migration must run after every "add" migration so it has clean state.

### Task 4.5: Run the test suite and fix anything that broke

- [ ] **Step 1: Run the migration tests**

```bash
pytest tests/test_migration_calibration.py -v
```

Expected: green, including the 5 new tests.

- [ ] **Step 2: Run the full test suite**

```bash
pytest tests/ -v 2>&1 | tail -50
```

Expected: green. If any test fails because it references dropped columns (`scope`, `topic_keys`, `topic_keys_status`), fix it inline by:
- Removing the kwarg from FeedbackEdit constructors.
- Removing the kwarg from Assignment constructors.
- Removing any assertion that reads those attributes.

- [ ] **Step 3: Boot the app and verify the migration runs cleanly**

```bash
rm -f /tmp/test_boot.db
DATABASE_URL=sqlite:////tmp/test_boot.db python -c "from app import app; print('boot ok')"
```

Expected: `boot ok`. Inspect the schema:

```bash
sqlite3 /tmp/test_boot.db ".schema feedback_edits" | grep -E 'scope|promoted'
sqlite3 /tmp/test_boot.db ".schema assignments" | grep -E 'topic_keys'
```

Expected: both empty (the migration dropped the columns).

```bash
sqlite3 /tmp/test_boot.db ".tables" | tr ' ' '\n' | grep -E 'subject_standards|subject_topic'
```

Expected: empty.

### Task 4.6: Commit

- [ ] **Step 1: Commit the migration and ORM trim**

```bash
git add db.py app.py tests/test_migration_calibration.py tests/test_calibration_intent.py
git commit -m "$(cat <<'EOF'
feat(db): drop subject_standards tables + obsolete FeedbackEdit/Assignment columns

Migration _migrate_drop_subject_standards (idempotent via MigrationFlag):
- DROP TABLE subject_standards, subject_topic_vocabulary
- DROP COLUMN feedback_edits.{scope, promoted_to_subject_standard_id,
  promoted_by, promoted_at}
- DROP COLUMN assignments.{topic_keys, topic_keys_status}
- Deactivate legacy promoted-only FeedbackEdits (scope='promoted' AND
  amend_answer_key=0) before dropping the column.

New _drop_columns helper handles both SQLite (table-rebuild dance) and
Postgres (native ALTER TABLE DROP COLUMN). Detects dialect via
db.engine.dialect.name. Idempotent: silently skips columns that
don't exist.

ORM trim: removed the same columns from the FeedbackEdit + Assignment
model classes. Removed scope='amendment' kwarg from the
FeedbackEdit construction in POST /result.

Tests: 5 new cases in tests/test_migration_calibration.py verifying
the drops + idempotency.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

After all 4 commits:

- [ ] **Step 1: Confirm branch state**

```bash
git log --oneline -6
```

Expected: the 4 new commits plus the spec commit (5 total since the start of this work) on top of the prior history.

- [ ] **Step 2: Run the full test suite one more time**

```bash
pytest tests/ -v 2>&1 | tail -20
```

Expected: green. Total test count: roughly the prior baseline minus the ~30 deleted tests, plus the 6 new propagation tests and 5 new migration tests.

- [ ] **Step 3: Manual smoke check on `sandbox_upgraded`**

```bash
python app.py
```

Open the teacher hub in a browser. Mark a submission (or use existing data). Edit a feedback line:
- One checkbox only.
- Save with checkbox OFF → no propagation banner.
- Save with checkbox ON → propagation banner with N similar count.
- Open a propagated submission → verify the field the teacher edited matches; the OTHER field is unchanged; marks may or may not have moved.

- [ ] **Step 4: Confirm we did NOT touch `staging`**

```bash
git branch -v | grep -E '^\* sandbox_upgraded'
git log staging --oneline -1 2>/dev/null
```

Expected: still on `sandbox_upgraded`. `staging` head unchanged from session start.

---

## Self-review notes

- All 11 sections of the design spec are covered by tasks:
  - §1 Problem — context only, no implementation needed.
  - §2 High-level approach — covered by the deletion table across commits 3 and 4.
  - §3 Data model — commit 4 (DDL) + ORM trim.
  - §4 Workflow — commits 1 (UI) and 2 (propagation logic).
  - §4.4 Field-aware + marks-aware contract — task 2.2 (signature) and 2.3 (worker).
  - §4.5 Effective answer key unchanged — verified by leaving `build_effective_answer_key` alone in task 3.3.
  - §5 Migration plan — tasks 4.1–4.6.
  - §6 Implementation sequence — 4 commits structured exactly as the spec requires.
  - §7 Capability changes — implicit in the deletions.
  - §8 Testing strategy — tasks 2.1 (new tests), 3.1 (deletions + refactor), 4.1 (migration tests).
  - §9 Rollback — each commit is independently revertable.

- `test_bank_amendment_push.py` is exercised in Task 2.4 Step 6 to make sure the amend flow still works. If those tests reference dropped columns, fix them in Task 4.5 Step 2.

- Type consistency: `target_field` parameter spelled identically in `refresh_criterion_feedback` signature (Task 2.2), the worker call (Task 2.3), and the test assertion (Task 2.1 last test). Return shape `{target_field: str, 'marks_awarded': int|None}` consistent across all three.
