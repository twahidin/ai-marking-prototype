# Port rubrics-redesign from staging onto sandbox_testing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring staging's rubrics-feedback-redesign onto sandbox_testing while preserving sandbox_testing's existing short-answer / corrections / iterative-attempts feature track. Branch by `assignment.assign_type`: short_answer keeps current behaviour everywhere; rubrics adopts staging's band-first redesign for both teacher and student modals.

**Architecture:**
- AI prompt builders, modal renderers, server emissions, and the student feedback view all gain an `assign_type == 'rubrics'` branch that uses staging's redesign code. Short-answer paths stay byte-identical except for one shared 3-line `submitted_at` fix and one bank.html UX win.
- Existing rubrics submissions on sandbox_testing predate the new fields. Renderer tolerates them by showing staging's "Re-mark to enable a specific description" affordance.
- Student rubrics view re-uses the teacher modal's `FeedbackRender.render()` with `editable: false` instead of the existing Layer 1/2/3 + Corrections layout.

**Tech Stack:** Flask, SQLAlchemy, Jinja2, vanilla JS (no framework), KaTeX shim. Source-of-truth for the redesign code is the staging branch at SHA `3231139` (origin/staging tip).

**Working state checkpoints:**
- Pre-port safety tag already in place: `sandbox_testing_pre_port_2026-05-07` → `b007b74`. Tag stays. Use `git reset --hard sandbox_testing_pre_port_2026-05-07` to roll the whole port back if needed.
- All commits below land directly on `sandbox_testing`. Do NOT push to `origin/sandbox_testing` until the final smoke-test task passes and the user explicitly confirms.

---

## Task 1: Cherry-pick standalone wins (bank pills + submitted_at fix)

**Files:**
- Cherry-pick: `templates/bank.html` + `app.py` (subject deduping for bank route)
- Modify: `app.py:6810-6813` (add `submitted_at` reset line in `teacher_submission_remark`)

- [ ] **Step 1.1: Cherry-pick `3231139` (bank filter pills) — confirmed clean in pre-flight**

```bash
git cherry-pick -x 3231139
```

Expected: clean apply. Touches `templates/bank.html` and a small `app.py` block adding subject collection for the bank route.

- [ ] **Step 1.2: Hand-apply the `submitted_at` fix from `972a443`**

Add line **after** `app.py:6812` (the `marked_at = None` line in `teacher_submission_remark`):

```python
    sub.submitted_at = datetime.now(timezone.utc)  # reset so 'stuck' clears
```

The companion fix in staging's `teacher_remark_all_submissions` will be applied in Task 7 when that endpoint is created.

- [ ] **Step 1.3: Sanity-check imports**

Run: `python -c "import app"` from repo root.
Expected: no ImportError. (`datetime` and `timezone` are already imported at the top of `app.py` — verify with `grep -n "from datetime" app.py | head -3`.)

- [ ] **Step 1.4: Commit**

```bash
git add app.py
git commit -m "fix(remark): reset submitted_at on re-mark so 'stuck' flag doesn't misfire

Hand-ported from staging 972a443. Companion fix in remark-all endpoint
lands in Task 7 when that route is added.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

(The cherry-pick from Step 1.1 is already its own commit.)

---

## Task 2: Add rubrics CSS scaffold

**Files:**
- Create: `static/css/feedback_render.css` (copy from staging — 396 lines, no adaptation needed)
- Modify: `templates/base.html` (add `<link>` after the existing CSS)

- [ ] **Step 2.1: Copy CSS file from staging**

```bash
git show staging:static/css/feedback_render.css > static/css/feedback_render.css
```

- [ ] **Step 2.2: Verify the file copied correctly**

Run: `wc -l static/css/feedback_render.css`
Expected: ~396 lines.

- [ ] **Step 2.3: Add `<link>` to `templates/base.html`**

Find the `<head>` block where existing stylesheets are linked. Insert after the last existing CSS `<link>`:

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/feedback_render.css') }}">
```

Use `git show staging:templates/base.html` and grep for `feedback_render.css` to find the exact context staging uses; mirror that placement.

- [ ] **Step 2.4: Smoke-check page loads**

Run: `python app.py` (in another terminal), browse to `/`. Confirm no 404 for `feedback_render.css` in browser DevTools network tab.

- [ ] **Step 2.5: Commit**

```bash
git add static/css/feedback_render.css templates/base.html
git commit -m "feat(rubrics): add feedback_render.css scaffold for redesigned modal

Verbatim copy from staging. CSS rules apply only when the rubrics modal
emits the new class names; other pages unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Replace rubrics AI prompt + rules

**Files:**
- Modify: `ai_marking.py` — `RUBRIC_FEEDBACK_RULES` constant (lines ~551–576) and `_build_rubrics_prompt()` (lines ~757–883)
- Modify: `ai_marking.py` — `mark_script()` signature/wrapper to accept `band_overrides` kwarg

**Constraint:** Do NOT modify `_build_short_answer_prompt`, `CORRECTION_PROMPT_RULES`, `IDEA_RULES`, or `FEEDBACK_GENERATION_RULES`. They remain in use for short_answer.

- [ ] **Step 3.1: Read both versions side-by-side**

```bash
git show staging:ai_marking.py | sed -n '500,950p' > /tmp/staging_ai_marking_excerpt.txt
sed -n '450,900p' ai_marking.py > /tmp/sandbox_ai_marking_excerpt.txt
```

Identify the exact line range of `RUBRIC_FEEDBACK_RULES` and `_build_rubrics_prompt` on the current sandbox_testing file.

- [ ] **Step 3.2: Replace `RUBRIC_FEEDBACK_RULES` with staging's version**

Use the Edit tool with the full multi-line `old_string` (sandbox's current `RUBRIC_FEEDBACK_RULES = """..."""` block) and `new_string` (staging's version). Staging's block includes:
- "no vague phrases" enforcement
- next_band_oneliner must be a question or directive
- rubric vocabulary verbatim
- match the student's level
- confidence sentinels (`__NO_CONFIDENT_QUOTE__`, `__NO_CONFIDENT_SUGGESTION__`)
- pair-2 optional rule

Source: `git show staging:ai_marking.py | sed -n '535,636p'`

- [ ] **Step 3.3: Replace `_build_rubrics_prompt` body with staging's version**

Replace the entire function body. Key changes:
- Adds `band_overrides` parameter (default None)
- Inlines teacher overrides into prompt when present (priority section telling AI not to re-decide those criteria)
- New JSON schema fields: `current_band_oneliner`, `next_band_oneliner`, `evidence_quote`, `improvement_target`, `improvement_rewrite`, `improvement_target_2`, `improvement_rewrite_2`, `maintain_advice`
- Removes `idea` and `correction_prompt` from rubrics schema (they remain only in short_answer)
- Keeps `feedback` and `improvement` fields (still used by PDF generator)

Source: `git show staging:ai_marking.py | sed -n '777,950p'`

- [ ] **Step 3.4: Plumb `band_overrides` through `mark_script`**

Find `def mark_script(...)` in `ai_marking.py`. Add `band_overrides=None` kwarg. Pass through to `_build_rubrics_prompt(..., band_overrides=band_overrides)` when assign_type == 'rubrics'.

Reference: `git show staging:ai_marking.py` and grep for `band_overrides` to see how staging wires it.

- [ ] **Step 3.5: Verify import / syntax**

Run: `python -c "import ai_marking; print(ai_marking.RUBRIC_FEEDBACK_RULES[:200])"`
Expected: no syntax error, prints first 200 chars of the new rules block (should mention "vague" / "rubric vocabulary" / sentinel strings).

- [ ] **Step 3.6: Commit**

```bash
git add ai_marking.py
git commit -m "feat(ai_marking): replace rubrics prompt with staging redesign (band-first)

Rubrics-mode prompt now emits current_band_oneliner, next_band_oneliner,
evidence_quote, improvement_target/rewrite pairs, and maintain_advice.
band_overrides param lets teacher lock bands and re-mark for fresh text.

Short-answer prompt unchanged — still emits feedback, improvement, idea,
correction_prompt for the existing Layer 1/2/3 student view + corrections
flow. Rubrics drops idea + correction_prompt; categorisation worker is
skipped for rubrics in a follow-up commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Skip categorisation worker for rubrics-mode

**Files:**
- Modify: `app.py:4770-4791` (`_kick_categorisation_worker`)

**Why:** The categorisation worker assigns `theme_key` + `specific_label` per question for the Mistake Category UI and themed correction prompts. Rubrics-mode in the redesign has no Mistake Category — bands are the primary axis — and no correction prompts. Running the worker on rubrics submissions would do useless DB writes and waste an AI call.

- [ ] **Step 4.1: Add early-return guard at top of `_kick_categorisation_worker`**

Edit `app.py` around line 4770. After the function signature and the existing submission lookup, add:

```python
    asn = Assignment.query.get(sub.assignment_id) if sub else None
    if asn and getattr(asn, 'assign_type', 'short_answer') == 'rubrics':
        return False
```

The exact insertion point depends on the current function shape — read the function first, then insert the guard immediately after the early-return for missing submission and before any work begins.

- [ ] **Step 4.2: Verify call sites still handle False return**

Grep for `_kick_categorisation_worker(` in app.py — there are 2 call sites (line ~4482 and ~7178). Confirm both treat the return value as a boolean (truthy = kicked, falsy = skipped). If either expects success specifically, the early-return False is still correct (it signals "didn't kick, don't wait").

- [ ] **Step 4.3: Commit**

```bash
git add app.py
git commit -m "feat(rubrics): skip mistake-category categorisation for rubrics submissions

Categorisation assigns theme_key + specific_label for the Mistake Category
UI and the themed correction-prompt picker. Rubrics-mode has neither —
bands are the primary axis, no Now You Try flow. Early-return saves an
AI call and a round of DB writes per rubrics submission.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Server-side rubric_pills emission

**Files:**
- Modify: `app.py` — add `_rubric_pills_for_questions()` helper (port from staging app.py:3987–4018)
- Modify: `app.py` — teacher_detail GET route to compute per-row rubric_pills dict and pass to template
- Modify: `templates/teacher_detail.html` — render pills in score cell when `assign_type == 'rubrics'`

- [ ] **Step 5.1: Port `_rubric_pills_for_questions` helper**

```bash
git show staging:app.py | sed -n '3987,4020p' > /tmp/rubric_pills_helper.py
```

Read it, then paste into sandbox_testing's `app.py` at an appropriate utility location (near other `_xxx_for_questions` helpers, or just above the teacher_detail GET route). The helper:
- Iterates `result_json['questions']`
- Pulls `criterion_name` and `band` (regex `Band (\d+)` for the number)
- Computes 2-char abbreviation (first word with ≥2 alpha chars, uppercase)
- Returns list of `{abbrev, band_num, criterion_name, band_label}` dicts

- [ ] **Step 5.2: Wire into teacher_detail GET route**

Find the route that renders `templates/teacher_detail.html`. For each submission with `status == 'done'` and `assignment.assign_type == 'rubrics'`, compute pills and assemble `rubric_pills_per_student = {student_id: [pill, ...]}`. Pass to template context.

Reference exact wiring: search staging's app.py for `rubric_pills_per_student` to see the loop body (commits `75ef47a` and `698f12f`).

- [ ] **Step 5.3: Render pills in `templates/teacher_detail.html` score cell**

Locate the score cell in the students table (the `<td>` showing marks per student). Wrap the existing content in `{% if assignment.assign_type == 'rubrics' and rubric_pills_per_student.get(student.id) %}` to render staging's `.score-stack` structure:

```html
<div class="score-stack">
  <div class="score-stack-total">{{ submission.marks_awarded }}/{{ submission.marks_total }}</div>
  <div class="score-stack-pills">
    {% for pill in rubric_pills_per_student[student.id] %}
      <span class="band-pill band-{{ pill.band_num }}" title="{{ pill.criterion_name }} — {{ pill.band_label }}">{{ pill.abbrev }}·B{{ pill.band_num }}</span>
    {% endfor %}
  </div>
</div>
```

`{% else %}` keeps the existing score cell HTML for short_answer + legacy rubrics rows.

Source for exact markup: `git show staging:templates/teacher_detail.html` — grep `score-stack`.

- [ ] **Step 5.4: Smoke-check**

Run app, view a class with a rubrics assignment (if one exists in your local dev DB). Confirm pills appear with band colors. If no rubrics assignment exists, defer this check to Task 12.

- [ ] **Step 5.5: Commit**

```bash
git add app.py templates/teacher_detail.html
git commit -m "feat(rubrics): server emits rubric_pills per student for table display

Per-criterion abbrev + band-number pill row in the score cell, only for
rubrics-mode assignments and only when the submission is fully marked.
Short-answer + legacy rubrics rows render the existing score cell.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Server — band-overrides on remark + band PATCH

**Files:**
- Modify: `app.py` — `teacher_submission_remark` endpoint (line 6797) to accept `band_overrides` JSON
- Modify: `app.py` — `_run_submission_marking` to accept and pass through band_overrides
- Modify: `app.py` — submission result PATCH endpoint (~line 6335) to accept `band` field on questions, capture `band_ai_original` on first override

- [ ] **Step 6.1: Read staging's remark endpoint + marking helper**

```bash
git show staging:app.py | sed -n '5268,5325p' > /tmp/staging_remark.py
git show staging:app.py | sed -n '3346,3510p' > /tmp/staging_run_marking.py
```

- [ ] **Step 6.2: Update `teacher_submission_remark` to read `band_overrides` from request JSON**

In `app.py:6797-6822`, before the `thread = threading.Thread(...)` line, add:

```python
    payload = request.get_json(silent=True) or {}
    band_overrides = payload.get('band_overrides') or {}
    if not isinstance(band_overrides, dict):
        band_overrides = {}
```

Pass to thread args: `args=(app, sub.id, assignment_id, band_overrides)`.

- [ ] **Step 6.3: Update `_run_submission_marking` to accept and pass `band_overrides`**

Add `band_overrides=None` param. Pass to `mark_script(..., band_overrides=band_overrides)`. After marking returns, defensively persist the chosen bands (clobber any criterion the AI tried to override back, and capture `band_ai_original` for the affected criteria). Match staging's defence-in-depth block (staging's lines 3433–3440).

- [ ] **Step 6.4: Update submission result PATCH to accept `band` field**

Find the PATCH route around `app.py:6335` (`teacher_submission_result_patch`). Add `band` (and `band_label`) to the allowed editable fields per question. On first override, before assigning the new band, capture the AI's original band into `q['band_ai_original']` if not already set:

```python
if 'band' in q_patch:
    if q.get('band_ai_original') is None:
        q['band_ai_original'] = q.get('band')
    q['band'] = q_patch['band']
```

Reference exact list of allowed fields in staging's PATCH route — also note that `current_band_oneliner`, `next_band_oneliner`, `evidence_quote`, `improvement_target`, `improvement_rewrite`, `improvement_target_2`, `improvement_rewrite_2`, `maintain_advice` should be added as editable string fields (with calibrate handling matching the existing `feedback`/`improvement` calibrate flow).

- [ ] **Step 6.5: Verify import / syntax**

Run: `python -c "import app"`
Expected: no error.

- [ ] **Step 6.6: Commit**

```bash
git add app.py
git commit -m "feat(rubrics): band-overrides on remark + band PATCH support

Re-mark endpoint now accepts band_overrides JSON; marking helper passes
to mark_script and defensively re-applies teacher's choices after the AI
returns. Result PATCH accepts band edits and captures band_ai_original
on the first teacher override so the modal can show a 'band manually
changed' indicator with a re-mark link.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Server — Re-mark All endpoint

**Files:**
- Add: new endpoint `/teacher/assignment/<id>/remark-all` POST in `app.py`

- [ ] **Step 7.1: Read staging's endpoint verbatim**

```bash
git show staging:app.py | sed -n '5320,5380p'
```

- [ ] **Step 7.2: Port the route into sandbox_testing's `app.py`**

Insert the route adjacent to `teacher_submission_remark` (after the force-remark endpoint near line 6850). Adapt:
- The eligible-submission query (status in `('done', 'error')`, has stored script)
- The reset block (status='pending', result_json=None, marked_at=None, **submitted_at=now_utc** — this is the second half of the `972a443` fix)
- Per-submission thread kick using `_run_submission_marking`
- Returns `{success: True, queued: N, skipped: M}`

- [ ] **Step 7.3: Verify import / syntax**

```bash
python -c "import app"
```

- [ ] **Step 7.4: Commit**

```bash
git add app.py
git commit -m "feat(remark): bulk Re-mark All endpoint for an assignment

POST /teacher/assignment/<id>/remark-all resets all done/error submissions
to pending and kicks one marking thread per. Skips submissions missing
stored script pages (legacy edge case). Resets submitted_at to clear
the stuck-indicator immediately.

Works for both short_answer and rubrics assignments. Companion of the
per-student remark fix landed earlier.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Teacher detail HTML — Re-mark All button

**Files:**
- Modify: `templates/teacher_detail.html` — Bulk Mark accordion (line ~440-480) to add Re-mark All button inline with Mark All Scripts

**Note:** sandbox_testing's Bulk Mark accordion exists at lines 440-473 with the `Mark All Scripts` button at line 473. Per staging commit `9d27dbc`, both buttons share the same horizontal row at the bottom of the accordion.

- [ ] **Step 8.1: Read staging's accordion block**

```bash
git show staging:templates/teacher_detail.html | sed -n '420,480p'
```

- [ ] **Step 8.2: Add Re-mark All button next to Mark All Scripts**

Replace the standalone `<button id="bulkMarkBtn">` line with a row that contains both buttons. The Re-mark All button:
- ID: `remarkAllBtn`
- Class: `action-btn` (secondary styling, not primary)
- onclick: `remarkAllSubmissions()`
- Disabled state: when `eligible_count == 0`
- Inline count badge: `Re-mark All ({{ eligible_count }})`
- Tooltip via `title=`: explain when to use it

You'll need to compute `eligible_count` (submissions with status in done/error that have stored script pages) in the route from Task 5 and pass to the template.

- [ ] **Step 8.3: Smoke check page renders**

Reload teacher_detail page in browser. Both buttons visible. Re-mark All disabled if no eligible submissions; enabled with a count otherwise.

- [ ] **Step 8.4: Commit**

```bash
git add app.py templates/teacher_detail.html
git commit -m "feat(teacher_detail): Re-mark All button inline with Mark All Scripts

Both buttons share the bottom row of the Bulk Mark accordion. Re-mark All
shows an inline count badge (eligible submissions) and a tooltip; disabled
when nothing is eligible. JS handler lands in the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Teacher detail JS — postRemark, remarkAllSubmissions, triggerRemark

**Files:**
- Modify: `templates/teacher_detail.html` — JS section (around lines 1300-1500)

- [ ] **Step 9.1: Read staging's JS block**

```bash
git show staging:templates/teacher_detail.html | sed -n '1430,1540p'
```

Identify staging's `postRemark`, `remarkStudent`, `remarkAllSubmissions`, `triggerRemark`, and `refreshSubmissionScore` functions.

- [ ] **Step 9.2: Extract `postRemark()` core helper**

Refactor sandbox's existing `remarkStudent` (line 1364-1386 area) by extracting the fetch-and-poll logic into `postRemark(submissionId, bandOverrides)`. `remarkStudent` becomes a thin wrapper that confirms the dialog then calls `postRemark`.

- [ ] **Step 9.3: Add `remarkAllSubmissions()`**

Paste staging's version (calls `/teacher/assignment/{id}/remark-all`, alerts queued/skipped count, reloads after 500ms).

- [ ] **Step 9.4: Add `window.triggerRemark` for feedback_render.js**

```javascript
window.triggerRemark = async function(submissionId, bandOverrides) {
    if (!submissionId) return;
    var msg = bandOverrides && Object.keys(bandOverrides).length
        ? 'Re-mark this student with your band override(s) locked? The AI will produce fresh descriptions and improvement examples anchored at your chosen band.'
        : 'Re-mark this student using the stored script?';
    if (!confirm(msg)) return;
    await postRemark(submissionId, bandOverrides);
};
```

- [ ] **Step 9.5: Add `refreshSubmissionScore` helper**

Port from staging — used by inline band/marks edits to refresh the score cell + pills without page reload. Calls the GET `/result` endpoint, rebuilds the score-stack DOM client-side.

- [ ] **Step 9.6: Wire `REMARK_ASSIGNMENT_ID` constant**

Staging's JS references `REMARK_ASSIGNMENT_ID`. Confirm it's already defined in sandbox_testing's template; if not, add `var REMARK_ASSIGNMENT_ID = {{ assignment.id|tojson }};` near the other top-of-script constants.

- [ ] **Step 9.7: Smoke check**

Reload teacher_detail. Open browser console. Confirm `typeof postRemark === 'function'`, `typeof remarkAllSubmissions === 'function'`, `typeof window.triggerRemark === 'function'`.

- [ ] **Step 9.8: Commit**

```bash
git add templates/teacher_detail.html
git commit -m "refactor(teacher_detail): extract postRemark + add bulk + trigger helpers

remarkStudent now wraps a shared postRemark(submissionId, bandOverrides).
remarkAllSubmissions wires the new bulk endpoint. window.triggerRemark
exposed for feedback_render.js's 'Re-mark for tailored text' link in the
band-stale notice. refreshSubmissionScore updates score cell + pills
client-side after inline band/marks edits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: feedback_render.js — rubrics rendering path

**Files:**
- Modify: `static/js/feedback_render.js` — add rubrics-redesign rendering, branch in `renderQuestion` / `renderShell`

This is the largest task. Strategy: ADD new functions for the redesigned rubrics rendering, then dispatch to them when `state.assignType === 'rubrics'` AND the new fields are present. Do NOT touch short_answer rendering.

- [ ] **Step 10.1: Read staging's rendering code**

```bash
git show staging:static/js/feedback_render.js > /tmp/staging_feedback_render.js
wc -l /tmp/staging_feedback_render.js  # ~2200 lines
```

Identify (using line numbers from the earlier brief):
- `renderRubricsHeaderSummary` (~lines 367-375)
- `renderRubricsTabStrip` + handler (~lines 376-404, 733-759)
- `renderRubricsCards` (~lines 405-488)
- `renderImprovementExamples` (~lines 500-553)
- `attachInlineEditTriggerHandler` (~lines 772-820) for click-to-edit band/marks
- Inline edit constructors `<select>` / `<input>` (~lines 1014-1083)
- `window.__rubricBandsByCriterion` global (~line 1339)
- `rubricsLegacyQCardHtml` body composer (~lines 565-635)

- [ ] **Step 10.2: Append rubrics-rendering functions to sandbox_testing's feedback_render.js**

Paste staging's rubrics-specific functions into sandbox_testing's file. Place them in a clearly-labelled section comment block:

```javascript
// ---------------------------------------------------------------
// Rubrics-redesign rendering (band-first modal). Active when
// state.assignType === 'rubrics' AND the new fields are present.
// Legacy rubrics submissions fall back to "Re-mark to enable".
// ---------------------------------------------------------------
```

Avoid duplicating any helpers that already exist on sandbox (e.g., `escapeHtml`, math preprocessing). If a helper name collides, prefer sandbox's version.

- [ ] **Step 10.3: Branch in `renderShell` for rubrics**

In `renderShell(state)` (sandbox line ~259), add at the top:

```javascript
if (state.assignType === 'rubrics' && _hasRubricsRedesignFields(state.questions)) {
    return renderRubricsShell(state);
}
```

Where `_hasRubricsRedesignFields(questions)` returns true if any question has `current_band_oneliner` OR `improvement_target` (key indicators that the new prompt produced this output).

`renderRubricsShell` is staging's redesigned shell (header summary + tab strip + active-tab body). Port it from staging's `renderShell` rubrics branch.

- [ ] **Step 10.4: Add legacy-rubrics fallback path**

When `state.assignType === 'rubrics'` but new fields ARE NOT present, render a banner card per criterion:

```javascript
function renderLegacyRubricsCard(q) {
    return '<div class="fb-card-current band-' + (bandNum(q.band) || 0) + '">' +
        '<div class="fb-card-label">' + escapeHtml(q.criterion_name || 'Criterion') + ' — ' + escapeHtml(q.band || '') + '</div>' +
        '<div class="fb-stale-notice">' +
        'Re-mark to enable a specific description. ' +
        '<a href="#" data-action="remark" data-submission-id="' + state.submissionId + '">Re-mark this student</a>' +
        '</div></div>';
}
```

The `[data-action="remark"]` link is wired by an existing handler that calls `window.triggerRemark(submissionId)`. Confirm or port the handler.

- [ ] **Step 10.5: Wire delegated click handlers**

Port from staging:
- Tab-strip click handler (active-tab swap)
- Inline-edit triggers (band dropdown, marks input)
- `[data-action="remark"]` link → `window.triggerRemark`
- Calibrate button handlers if any new fields need calibrate support

- [ ] **Step 10.6: Set `window.__rubricBandsByCriterion` from server data**

The inline band dropdown reads `window.__rubricBandsByCriterion` to know which bands are valid per criterion. The teacher_detail GET route must pass this data; the modal-open call site (in teacher_detail.html `openFeedbackModal`) sets the global before calling render. Port the wiring from staging.

- [ ] **Step 10.7: Confirm short_answer rendering untouched**

```bash
git diff static/js/feedback_render.js | grep -E "^\-" | grep -v "^---" | head -30
```

Lines starting with `-` indicate removals. Verify no short_answer-related code was deleted — only additions and the conditional branch in `renderShell`.

- [ ] **Step 10.8: Commit**

```bash
git add static/js/feedback_render.js
git commit -m "feat(rubrics): redesigned modal rendering — band-first cards + tabs

renderRubricsShell, renderRubricsHeaderSummary, renderRubricsTabStrip,
renderRubricsCards, renderImprovementExamples ported from staging.
Active when assignType === 'rubrics' AND the new prompt fields are
present. Legacy rubrics submissions show a 'Re-mark to enable' affordance
with a link wired to window.triggerRemark.

Short-answer rendering untouched. Mistake Category, Layer 3 idea,
Corrections rendering remain available for short_answer assignments.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Student feedback view branches by assign_type

**Files:**
- Modify: `app.py` — `/feedback/<assignment_id>/<int:submission_id>` route (line ~7119)
- Modify: `templates/feedback_view.html` (or add a sibling template)

Per design: short_answer keeps the existing Layer 1/2/3 + Corrections layout. Rubrics renders the same redesigned modal as the teacher, read-only, by calling `FeedbackRender.render(container, result, {editable: false})`.

- [ ] **Step 11.1: Decide template strategy**

Two options. Pick (A) for minimum churn:

- **(A) Branch inside `feedback_view.html`** — at the top of the body, `{% if assignment.assign_type == 'rubrics' %}` show a single container `<div id="rubricsFeedbackContainer">` and load `feedback_render.js`, then call `FeedbackRender.render(container, RESULT, {editable: false, assignmentId: ..., submissionId: ..., scoringMode: ...})`. Else render the existing Layer 1/2/3 + Corrections layout.

- (B) Sibling template `feedback_view_rubrics.html` — duplicate of base.html shell but only the rubrics container. Route picks one or the other.

- [ ] **Step 11.2: Implement option (A)**

Wrap the entire existing body of `feedback_view.html` in `{% if assignment.assign_type != 'rubrics' %}...{% endif %}`. Add the rubrics branch before/after with the FeedbackRender container.

The rubrics branch needs:
- `<link>` to `css/feedback_render.css` (already in base.html if Task 2 done)
- `<script>` for `feedback_render.js`
- A small inline `<script>` that grabs `RESULT` (Jinja-serialised result_json), passes to FeedbackRender.render
- Server already passes `assignment` to the template — verify

- [ ] **Step 11.3: Confirm route passes assignment object to template**

In `app.py:7119`, find the `render_template('feedback_view.html', ...)` call. Confirm `assignment=asn` is in kwargs. If not, add it.

- [ ] **Step 11.4: Smoke check**

If a rubrics assignment + submission exists locally, navigate to its student feedback URL. Confirm redesigned modal renders, no edit handles, no remark links visible (editable: false).

If no rubrics submission exists, defer to Task 12.

- [ ] **Step 11.5: Commit**

```bash
git add app.py templates/feedback_view.html
git commit -m "feat(student): rubrics view uses the redesigned teacher modal read-only

Student opening feedback for a rubrics assignment now sees the same
band-first modal teachers see, with editable: false. Short-answer view
unchanged — Layer 1/2/3 + Corrections + Now You Try preserved.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Smoke test — end-to-end

**No code changes.** Manual / browser verification.

- [ ] **Step 12.1: Start the dev server**

```bash
python app.py
```

Open browser at `http://localhost:5000`.

- [ ] **Step 12.2: Verify short_answer flow unchanged**

- Mark a short_answer assignment (single script).
- Open teacher modal: Mistake Category dropdown present, Layer 3 idea expander present, theme calibrate works.
- Open student view: Layer 1/2/3 verdict + criterion cards + "Why does this matter?" + "Now You Try" all present.
- Submit a correction attempt as student. Confirm verdict bubble appears.

If any of the above breaks, **STOP** and root-cause before continuing.

- [ ] **Step 12.3: Verify rubrics flow with redesign**

- Create or reuse a rubrics assignment.
- Mark a script.
- Open teacher modal: tab strip per criterion, band-first cards, evidence/improvement columns, click-to-edit band dropdown + marks, Re-mark for tailored text link if band edited.
- Edit a band. Confirm score cell + pills refresh client-side without page reload.
- Click "Re-mark for tailored text". Confirm the band override is sent and the AI re-marks anchored at the chosen band.
- Open student view for the same submission. Confirm the same modal renders read-only.

- [ ] **Step 12.4: Verify legacy rubrics fallback**

If a pre-port rubrics submission exists, open it. Confirm "Re-mark to enable a specific description" notice with a remark link is shown. Click the link, confirm re-mark happens, confirm the new fields populate after.

- [ ] **Step 12.5: Verify Re-mark All**

In a class with multiple done submissions, click "Re-mark All". Confirm count badge matches eligible submissions, modal asks for confirmation, all rows show "Marking..." after, table reflects new statuses on reload.

Run for both a short_answer and a rubrics assignment.

- [ ] **Step 12.6: Verify bank filter pills**

Navigate to `/bank`. Confirm both Level pills and Subject pills appear. Click a Level — only that level's cards remain. Click a Subject — both filters intersect. URL `?level=g3` should pre-select the G3 pill on page load.

- [ ] **Step 12.7: Verify PDF report still works**

Generate a PDF report for both a short_answer submission and a rubrics submission. Both should download and render without error. Rubrics PDF will show feedback + improvement (the new band-specific fields are intentionally modal-only).

- [ ] **Step 12.8: Run a syntax/import sanity check**

```bash
python -c "import app; import ai_marking; import pdf_generator; print('OK')"
```

Expected: `OK`.

- [ ] **Step 12.9: Note any deferred or broken items**

If any step in 12.2-12.7 fails, write a short note in this plan doc describing the failure and a follow-up plan. Do NOT push to origin until everything passes.

- [ ] **Step 12.10: Final commit (if any cleanup)**

If the smoke test surfaced any small fixes, commit them as a final `chore` or `fix` commit. Otherwise no commit needed for Task 12.

---

## Task 13: Push to origin (gated on user confirmation)

**No code changes.** Push gate.

- [ ] **Step 13.1: Show user the new commit list**

```bash
git log --oneline sandbox_testing_pre_port_2026-05-07..sandbox_testing
```

- [ ] **Step 13.2: Wait for explicit user "ok push"**

Memory rule: never push to staging without confirmation. Same prudence applies to sandbox_testing for a port of this size — the user should review the commit list and the smoke-test outcomes before they go to origin.

- [ ] **Step 13.3: Push**

```bash
git push origin sandbox_testing
```

- [ ] **Step 13.4: Drop the safety tag (optional)**

Once the user confirms the port works on Railway / staging deploy:

```bash
git tag -d sandbox_testing_pre_port_2026-05-07
```

---

## Self-review notes

- **Spec coverage:** Every staging commit from today that the user wants is covered. The 23 rubrics-redesign commits collapse into Tasks 3, 5, 6, 7, 8, 9, 10, 11. The 4 standalone wins from this morning are in Task 1 (bank pills, submitted_at) and Tasks 7+8 (Re-mark All). Skipped: the "Re-mark All standalone card below accordion" (`14fb024`) — superseded by the inline `9d27dbc` UX which is what gets ported.
- **Legacy-rubrics path:** Task 10 Step 10.4 covers the "Re-mark to enable" affordance for old submissions, per user's confirmation.
- **Categorisation skipped:** Task 4 covers it.
- **Student modal = teacher modal for rubrics:** Task 11 covers it.
- **Risk hotspots:** Task 10 (largest patch, JS rendering) and Task 6 (PATCH endpoint signature changes). Both have explicit smoke checks in Task 12.
- **Reversibility:** safety tag in place; every task is a discrete commit; `git reset --hard sandbox_testing_pre_port_2026-05-07` rolls everything back.
