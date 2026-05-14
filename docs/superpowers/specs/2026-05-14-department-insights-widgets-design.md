# Department Insights — band-tabbed customisable widget dashboard

**Status:** Draft for review
**Author(s):** Joe Tay (product), Claude (design)
**Date:** 2026-05-14
**Branch:** sandbox_upgraded

## 1. Problem

`/department/insights` today is a flat page with three charts (class comparison, score distribution, question difficulty), an AI analysis block, and an item-analysis tool. It exposes raw statistics but does little to answer the question the HOD actually asks: *"what should the department do this week to better support our teachers and students?"*

Three concrete gaps:

1. **No level grouping.** Sec 1–5 are mashed together. Departmental conversations almost always happen per level band (Sec 1 / 2 / 3 / 4-5), and the page can't filter to that view.
2. **Surveillance-shaped widgets.** The natural extensions of today's charts (teacher latency, override rate, persistent-struggler lists) push the HOD toward auditing individuals. The HOD's actionable levers are systemic — curriculum, resources, calibration alignment, PD, scheduling — not "go ask Mrs. Tan about her class."
3. **No customisation.** Different roles want different views. A subject lead wants calibration health; an HOD doing pastoral review wants capacity signals. One fixed layout serves neither well.

Out of scope (deferred to v2+):

- Cross-cohort delta (this-year-vs-last-year for the same level + subject)
- Teacher-initiated intervention requests
- Recent annotations / known-issues feed on assignments
- AI confidence field on the marking output
- A separate Subject-Head insights page (see §9 note on the SH "own page" follow-up)

## 2. High-level approach

Rebuild the page around three ideas:

1. **Level-band tabs.** Pinned at the top: `Sec 1 · Sec 2 · Sec 3 · Sec 4/5 · All`. Switching tab re-queries data; layout stays.
2. **Customisable GridStack dashboard.** Same paradigm as `teacher_insights.html`: drag, resize, recolour, remove, add. One starter template applied on first visit; HOD can apply other templates or build from scratch. Layout is stored once per teacher and is **identical across all band tabs** — only the data swaps.
3. **Department-shaped widgets only.** Every widget answers a question the HOD can act on without summoning an individual teacher. No widget surfaces a teacher's name or a single student's record. Aggregate, systemic, action-oriented.

The Band Health Strip is the one non-removable widget (it's the band's vitals row). Everything else is fully editable.

## 3. Page structure

### 3.1 Toolbar

```
┌──────────────────────────────────────────────────────────────────┐
│ Department Insights              Overview | Department | My Class│
│ [Sec 1] [Sec 2] [Sec 3] [Sec 4/5] [All]                [⚙ Edit] │
└──────────────────────────────────────────────────────────────────┘
```

- Three-segment toggle (Overview / Department / My Class) — unchanged.
- Band tabs as pill-segmented control. Active state filled `#667eea`.
- Edit button toggles `body.edit-mode`. In edit mode: each tile gets the dashed outline, `×` remove, and 🎨 swatch chrome already styled in `teacher_insights.html`. The "Add widget" + "Starter templates" panel slides open above the grid.

### 3.2 Level-band resolution

A `Class.level` free-text string is normalised to a band key by `resolve_band(level: str) -> str`:

```
'sec 1', 'sec1', '1', 'Secondary 1', '1A', '1E5'   → 'sec1'
'sec 2', 'sec2', '2', 'Secondary 2', '2T'          → 'sec2'
'sec 3', 'sec3', '3', '3 Express'                   → 'sec3'
'sec 4', 'sec5', '4N', '5', '4/5'                  → 'sec45'
anything else (or empty)                            → 'unbanded'
```

Notes:

- Sec 4 and Sec 5 collapse to one band per the brief (similar syllabuses).
- `unbanded` is silently included in the `All` tab; given its own pseudo-tab only if any class resolves to it.
- The resolver lives in a new module `bands.py` so other surfaces (the dashboard filter, future widgets) can reuse it.

### 3.3 Edit mode

Identical interaction model to `teacher_insights.html`:

- Drag any tile to reposition; drag any edge to resize (respects `minW`/`minH`).
- `×` removes the tile (no confirmation; user is in edit mode and can re-add).
- 🎨 opens an inline swatch popover with the seven pastels + clear.
- Add Widget panel: chips of every widget; click adds, click again removes. Already-on-grid widgets show their chip as active.
- Starter templates: four named layouts (see §5). Applying one replaces the current layout after a `confirm()`.
- Exiting edit mode auto-saves layout to `DepartmentDashboardLayout`.

The Band Health Strip is rendered as a GridStack tile like the others but its `×` button is suppressed; its color and position are still editable.

### 3.4 Mobile fallback

Same approach as `teacher_insights.html` — `@media (max-width: 600px)` hides `#dashboardGrid` and shows an "Open Insights on a desktop" card.

## 4. Widget catalog

For each widget: title, default grid size (`w × h`, `minW × minH`), data source, computed metric, minimum-sample threshold, edge-case fallback, and edit affordances.

All widgets except `band_health` are removable, draggable, resizable, recolourable.

### 4.1 `band_health` — Band Health Strip (non-removable)

- **Default size:** `w12 h3`, `minW 6 minH 3`.
- **Source:** `Class`, `Assignment`, `Submission`, `Teacher` filtered to active band tab.
- **Computes:**
  - `classes_count`, `teachers_count` (distinct via `TeacherClass`), `assignments_term_count` (assignments with ≥1 submission, in current term window).
  - `avg_score`: submission-weighted mean of per-submission %-score across all final submissions in band, term window. Uses existing scoring logic (marks → marks_awarded/marks_total; status → correct/total).
  - `submission_rate`: `marked_count / (students_in_band × assignments_issued_in_band)`, capped at 100%.
  - `trend`: delta of `avg_score` last 30d vs prior 30d. Sparkline = weekly avg over last 8 weeks.
  - `vs_last_term`: `avg_score` and `submission_rate` deltas vs same metric in the prior term window. Hidden if no prior data exists.
- **Min sample:** trend chip hidden if `assignments_in_window < 3`.
- **Empty state:** "No data in this band yet."
- **Render:** three big numbers in row; deltas in muted text below; sparkline + arrow chip on the right.

### 4.2 `dept_goals` — Departmental Goals

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** new `DepartmentGoal` table.
- **Computes per goal:** progress derived from `metric_type`:
  - `pass_rate`: % of submissions ≥50% across the goal's scope (target_band/target_subject if set, else all bands/subjects) over the current term.
  - `avg_score`: submission-weighted mean within scope.
  - `submission_rate`: marked/(students × assignments) within scope.
  - `exemplar_coverage`: % of scope's vocabulary topics with `Assignment.exemplar_analysis_json IS NOT NULL` on at least one assignment.
  - `bank_coverage`: % of scope's vocabulary topics with ≥1 active `SubjectStandard`.
- **Status:** `done` ≥100% · `on track` ≥80% of target · `behind` 50–79% · `off track` <50%. Coloured bar accordingly.
- **CRUD:** small `+ Add goal` button opens a modal (title, metric_type, target_value, optional target_band, optional target_subject). The modal's defaults depend on viewer role:
  - **HOD / Lead:** target_subject defaults to "All subjects", target_band defaults to "All bands". Both can be overridden.
  - **Subject Head:** target_subject defaults to the SH's primary subject (resolved from any class they teach, falling back to a dropdown if ambiguous), target_band defaults to "All bands". Subject can still be re-picked but the SH cannot create a goal that has no subject set (i.e. cannot create dept-wide-all-subjects goals).
  - Soft delete only.
- **Permissions (v1):** create/edit/delete restricted to `Teacher.role in ('hod', 'subject_head', 'lead')`. `manager` and `owner` see goals read-only.
- **Goal visibility:** the widget shows **every** active (non-soft-deleted) goal regardless of who created it. Rendered order: dept-wide goals (no target_subject) first, then subject-specific. Each row labels its scope with a small chip ("All subjects · Sec 3", "Chemistry · Sec 4/5", etc.).
- **Empty state:** "Add your first department goal."
- **Render:** stacked rows. Title · scope chip · progress bar · `X / Y` · status chip.
- **v2 follow-up:** Subject Heads asked for "their own page" to manage their goals. v1 reuses the dept insights page; v2 may add `/subject/insights` with the same widget paradigm scoped to the SH's subject(s). The schema (`target_subject` column) is already shaped to support that move.

### 4.3 `score_distribution` — Score Distribution (band)

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** `Submission` filtered to active band tab, current term window.
- **Computes:** five buckets `0–20`, `21–40`, `41–60`, `61–80`, `81–100`. Count per bucket. Bars are coloured red → orange → yellow → green → purple matching the existing distChart in `department_insights.html`.
- **Min sample:** chart hidden if total submissions < 10. Show "Not enough submissions yet" placeholder.
- **Empty state:** as above.
- **Render:** vertical bars; bucket label + count above each bar. (V1 ships **single-tone** bars — the ghosted overlay was paired with the deferred cross-cohort delta; without that we just show this-term as-is. Two-tone overlay revisited when cross-cohort lands.)

### 4.4 `ai_analysis` — AI Analysis

- **Default size:** `w12 h7`, `minW 6 minH 6`.
- **Source:** consolidated payload from other widgets (sticky topics, score distribution, coverage gaps, alignment, partial-credit hotspots, frequently amended).
- **Generation:** button-triggered. Cached in `DepartmentConfig` keyed by `dept_ai_analysis:band={band}` so navigating away and back re-shows the last analysis.
- **System prompt hardening (required):**
  1. Lead with `## Important caveats`: list any widgets where `min_sample` triggered and a one-line reason.
  2. **Forbidden** to name an individual teacher, class, or student as a cause or recommended target.
  3. Tag every recommendation with one of `[Curriculum] [Resource] [PD] [Scheduling]`.
  4. End with the literal sentence `These are hypotheses for department discussion, not conclusions.`
- **Client-side guard:** post-generation regex sweep for class names (from `Class.name`) and teacher names (from `Teacher.name`); if any appear, re-prompt once with a stronger negative instruction. If still present, surface the response with a warning banner asking the HOD to regenerate.
- **Render:** caveats banner (amber), summary paragraph, action items as colour-tagged chips, footer with provider/model/timestamp.

### 4.5 `sticky_topics` — Sticky Topics

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** `Assignment.topic_keys` (status `ready` only) and `Submission.result_json` in band, current term.
- **Computes:** for each topic_key appearing in band assignments:
  - `assignments_count`: count of distinct assignments tagged with it.
  - `submissions_count`: count of final submissions across those assignments.
  - `mastery`: mean of per-question correctness across all questions in those submissions, **weighted uniformly per assignment** (so one large class doesn't dominate one small class).
- **Inclusion rule:** `assignments_count ≥ 3` AND `submissions_count ≥ 10` AND `mastery < 60%`.
- **Tag:** `chronic` if `assignments_count ≥ 4`, `emerging` if exactly 3.
- **Empty state:** "No sticky topics — keep an eye out."
- **Render:** table-like rows: topic name · mastery bar · `Nx` count · status chip. Limit to top 8 by lowest mastery.

### 4.6 `topic_coverage_map` — Topic Coverage Map

- **Default size:** `w12 h5`, `minW 6 minH 4`.
- **Source:** `SubjectTopicVocabulary` for subjects taught in the band, intersected with `Assignment.topic_keys` (status `ready`) per fortnight.
- **Bins:** fortnight columns from `DepartmentConfig.term_start_date` (or the rolling fallback of `today − 26 weeks`) through today. A Singapore term is typically 5 fortnights; a semester is ~10–13. The header renders one column per fortnight from start to now.
- **Cell rule:** shaded if ≥1 assignment in band was created in that fortnight AND has the topic in its topic_keys.
- **Empty state:** "No vocabulary loaded for the band's subjects yet."
- **Render:** matrix; topic name rows; dot per shaded cell. Empty rows displayed (the gap is the point). Hover reveals assignment titles in that cell.

### 4.7 `topics_no_exemplars` — Topics Needing Exemplars

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** `SubjectTopicVocabulary` for band's subjects vs `Assignment.exemplar_analysis_json IS NOT NULL`.
- **Computes:** for each topic, count of assignments-tagged-with-this-topic that have `exemplar_analysis_json` populated. Topics with count `0` or `1` are listed.
- **Empty state:** "All topics have exemplars."
- **Render:** list. Each row: topic name + small badge (`no exemplar` / `1 exemplar*`). Footnote: `* single-source; aim for 2+ per topic`.

### 4.8 `frequently_amended` — Items to Re-Review

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** `FeedbackEdit` rows joined to `Submission` joined to `Assignment` in band.
- **Computes:** per `(assignment_id, question_number)`, `rate = edit_count / submission_count_for_that_assignment`. Include only pairs where submissions ≥ 5.
- **Render:** top 8 rows by rate. `Q{n} · "{asn.title}"` · progress bar · `X%`. Footnote explains the metric and that no teacher is named.

### 4.9 `bank_coverage` — Bank Coverage

- **Default size:** `w4 h5`, `minW 3 minH 4`.
- **Source:** `SubjectStandard` (active=True) vs `SubjectTopicVocabulary` for band's subjects.
- **Computes:** `covered / total` where covered = topics with ≥1 active standard.
- **Snapshot for delta:** at term start, snapshot count is written to `DepartmentConfig` key `bank_coverage_snapshot:term_{n}:band_{b}`. Delta vs current = `+X vs last term`.
- **Render:** donut + `X / Y` + `XX%` + delta line. Falls back to "Vocabulary not loaded yet" if denominator = 0.

### 4.10 `calibration_alignment` — Calibration Alignment

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** `FeedbackEdit.theme_key` joined to `Submission` joined to `Assignment` in band.
- **Computes:** for each topic_key with FeedbackEdits in band, build a `{teacher_id → top_theme_key}` map where each teacher contributes their most-common theme. Require teachers with ≥3 edits on the topic to contribute. Alignment % = `count(teachers sharing top-1 OR top-2 modal theme) / count(contributing teachers)`.
  - Bucket: `high ≥ 75%`, `mid 50–74%`, `low < 50%`.
- **Min sample:** row hidden if fewer than 2 contributing teachers on the topic.
- **Render:** 9-dot scale per topic + word label + optional "joint mark?" suggestion on low rows. **No teacher names anywhere in the DOM or in the data payload.**
- **Empty state:** "Not enough calibration data across teachers yet."

### 4.11 `bank_growth` — Bank Growth

- **Default size:** `w6 h4`, `minW 4 minH 3`.
- **Source:** `SubjectStandard.created_at`, department-wide (not band-scoped — bank is a dept resource).
- **Computes:** weekly counts for last 13 weeks (rolling — not term-aligned, so growth momentum is visible across term boundaries).
- **Render:** vertical bars. Footer: `This term: X standards · +Y vs last term`.
- **Empty state:** zero-bars with "Bank empty — calibration just getting started" caption.

### 4.12 `partial_credit_hotspots` — Partial-credit Hotspots *(reframed from "AI Uncertainty")*

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** `Submission.result_json` questions joined to `Assignment.topic_keys` in band.
- **Computes:** per topic_key, `partial_rate = count(questions where status='partial' OR (marks_awarded between 10% and 90% of marks_total)) / total_questions_in_topic`.
- **Min sample:** topic excluded if `total_questions_in_topic < 10`.
- **Render:** bar per topic, ordered by partial_rate desc. Top 8. Caption: *"These topics often land in the messy middle — worth reviewing the rubric or model answers."*
- **Rationale:** AI confidence isn't stored per question today. Partial-credit concentration is a defensible substitute that uses existing data.

### 4.13 `wins_to_share` — Wins Worth Sharing

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** derived from #5 (sticky topics), #10 (alignment), band-level submission-rate trend, #11 (bank growth).
- **Computes (each item is independent):**
  - **Topic moved off sticky list:** topic was sticky 30 days ago but isn't today.
  - **Alignment improved:** topic moved from `low` to `mid` or `high` in last 30 days.
  - **Submission rate up:** band submission rate in last 4 weeks is ≥5pp higher than the prior 4 weeks.
  - **Bank growth spike:** week with ≥5 new standards in the last 30 days.
- **Render:** up to 5 bullets with `⬆` glyph and the supporting datum (e.g., "Equilibrium mastery rose 18pp band-wide"). No teacher names.
- **Empty state:** "Quiet week — keep going."

### 4.14 `persistent_gap` — Persistent Gap (band)

- **Default size:** `w4 h5`, `minW 3 minH 4`.
- **Source:** `Submission` joined to `Student` in band.
- **Computes:** denominator = students in band with ≥4 final submissions ever. Numerator = those students with `<40%` on ≥3 of their last 4 assessments. Result = `numerator / denominator` as a single %.
- **Min sample:** widget shows "Not enough assessment history yet" if `denominator < 0.5 × students_in_band`.
- **Render:** single big number. Caption with denominator: `"of N Sec 2 students with at least 4 assessments"`. Static suggested supports below: `after-school clinic · peer tutoring`.

### 4.15 `marking_pipeline` — Marking Pipeline

- **Default size:** `w6 h5`, `minW 4 minH 4`.
- **Source:** `Submission` in band, last 14 days vs prior 90 days baseline.
- **Computes:**
  - `submitted`: count(`Submission.created_at` in last 14d).
  - `marked`: count(`status='done' AND is_final=True` in last 14d).
  - `pending`: `submitted - marked`.
  - `pending_share_now`: `pending / submitted`.
  - `pending_share_baseline`: same ratio over prior 90 days (rolling).
- **Min sample:** widget shows "Quiet fortnight" placeholder if `submitted < 10`.
- **Render:** three horizontal stacked-style bars. Caption: `Pending share: X% (last term: Y%) — department aggregate, not per teacher.`

### 4.16 `assessment_rhythm` — Assessment Rhythm

- **Default size:** `w6 h4`, `minW 4 minH 3`.
- **Source:** `Assignment.created_at` in band, current term.
- **Computes:** count of distinct assignments (with ≥1 submission) per fortnight bin. Department-wide median of `assignments_per_fortnight` across all bands shown as reference line.
- **Render:** small bar chart. Footer: `Median: X/fortnight · This band: Y ✓`.

## 5. Starter templates

Four opinionated layouts the HOD can apply with one click. Same shape as `STARTER_TEMPLATES` in `teacher_insights.html`. First-time visitor gets `overview` auto-applied.

| Key | Label | Widgets (in grid order) |
|---|---|---|
| `overview` *(default)* | Overview | band_health · dept_goals · score_distribution · sticky_topics · ai_analysis · wins_to_share |
| `curriculum` | Curriculum-focused | band_health · sticky_topics · topic_coverage_map · topics_no_exemplars · assessment_rhythm · ai_analysis |
| `calibration` | Calibration-focused | band_health · calibration_alignment · bank_coverage · bank_growth · frequently_amended · partial_credit_hotspots |
| `capacity` | Support & Capacity | band_health · persistent_gap · marking_pipeline · assessment_rhythm · wins_to_share · ai_analysis |

Each template's layout JSON is committed in the template constant.

## 6. Schema additions

```python
class DepartmentDashboardLayout(db.Model):
    """One layout per HOD/SH/Lead/Manager/Owner viewer. Shared across band tabs.

    layout_json: list of {key, x, y, w, h, color} dicts emitted by GridStack.
    last_band: 'sec1' | 'sec2' | 'sec3' | 'sec45' | 'all' — restored on next visit.
    """
    __tablename__ = 'department_dashboard_layout'
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'),
                           primary_key=True)
    layout_json = db.Column(db.Text, nullable=False, default='[]')
    last_band = db.Column(db.String(20), default='sec1')
    updated_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))


class DepartmentGoal(db.Model):
    """HOD/SH/Lead-set goals shown in the dept_goals widget."""
    __tablename__ = 'department_goal'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    metric_type = db.Column(db.String(40), nullable=False)
    # one of: pass_rate, avg_score, submission_rate, exemplar_coverage, bank_coverage
    target_value = db.Column(db.Float, nullable=False)
    target_band = db.Column(db.String(20), nullable=True)
    # nullable → all bands
    target_subject = db.Column(db.String(200), nullable=True)
    # nullable → all subjects (HOD/Lead only; SH must set a subject)
    created_by_id = db.Column(db.String(36), db.ForeignKey('teachers.id'),
                              nullable=False)
    created_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc))
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
```

`DepartmentConfig` gains soft keys (no schema change):

- `term_start_date` → ISO date string. Drives fortnight binning, "current term" windows. If absent: rolling fallback (`max(Assignment.created_at) − 26 weeks`).
- `bank_coverage_snapshot:term_{n}:band_{b}` → integer count, written at the start of each term (or on first widget render of a new term).
- `dept_ai_analysis:band={band}` → JSON cache of last AI analysis per band.

## 7. Data endpoint

Single endpoint to keep the page snappy. Returns all widget payloads for one band tab.

```
GET /department/insights/widgets?band={sec1|sec2|sec3|sec45|all}

200 {
  "band": "sec1",
  "band_label": "Sec 1",
  "as_of": "2026-05-14T16:02:00Z",
  "term_window": {"start": "2026-01-06", "fortnight_index": 6},
  "band_health": {...},
  "dept_goals": [{...}],
  "score_distribution": {"buckets": {...}, "total": 187},
  "sticky_topics": [{...}],
  "topic_coverage_map": {"topics": [...], "fortnights": [...], "cells": [[bool]]},
  "topics_no_exemplars": [{...}],
  "frequently_amended": [{...}],
  "bank_coverage": {"covered": 31, "total": 42, "delta_vs_last_term": 4},
  "calibration_alignment": [{...}],
  "bank_growth": {"weekly": [...], "term_total": 31, "delta_vs_last_term": 12},
  "partial_credit_hotspots": [{...}],
  "wins_to_share": [{...}],
  "persistent_gap": {"pct": 12, "n_qualified": 84, "n_band_total": 96},
  "marking_pipeline": {"submitted": 187, "marked": 142, "pending": 45,
                       "pending_share_now": 0.24, "pending_share_baseline": 0.18},
  "assessment_rhythm": {"weekly": [...], "median_across_bands": 3.0,
                        "this_band": 3.2},
  "low_sample_widgets": ["score_distribution"]
}
```

`low_sample_widgets` is what the AI analysis widget reads when generating the caveats banner.

Layout endpoints:

```
GET  /department/insights/layout      → {layout: [...], last_band: "sec1"}
PUT  /department/insights/layout      → body: {layout: [...], last_band: "sec1"}
POST /department/insights/layout/reset → restores last_band, layout=overview template
```

Goals endpoints (HOD/SH/Lead only for mutating):

```
GET    /department/insights/goals
POST   /department/insights/goals
PATCH  /department/insights/goals/<id>
DELETE /department/insights/goals/<id>   (soft delete)
```

Server-side authz on POST/PATCH/DELETE:

- `hod`, `lead`: may create/edit/delete any goal.
- `subject_head`: may create/edit/delete only goals where `target_subject` is set and matches a subject they teach. Cannot create dept-wide-all-subjects goals.
- Anyone else who passes `_require_insights_access`: read-only via GET.

AI analysis (unchanged shape, new prompt):

```
POST /department/insights/analyze
  body: {band, provider, model}
  → calls AI with the hardened system prompt + the widgets payload
```

## 8. AI prompt — hardened

```
SYSTEM:
You are an analyst for a school department's marking insights page. The viewer is
the HOD or a subject lead. Your job is to help them make systemic decisions about
curriculum, shared resources, professional development, and scheduling.

ABSOLUTE RULES:
1. Open with `## Important caveats`. List low-sample data exclusions and any
   teacher-supplied known-issue annotations (none in v1). If there are no
   caveats, write "None for this view."
2. Never name an individual teacher, class, or student as a cause or as the
   target of an action. If the data implies one, talk about the band as a whole.
3. Tag every recommendation with exactly one of: [Curriculum] [Resource] [PD]
   [Scheduling]. Reject the temptation to invent other tags.
4. End with the literal sentence: "These are hypotheses for department
   discussion, not conclusions."

DATA: <widgets payload as JSON>
```

The client runs a final regex sweep on the response. If it finds any token that
matches a current `Teacher.name` or `Class.name`, it re-prompts once. If the
second attempt also fails, render the response inside an amber "Please
regenerate" banner.

## 9. Permissions

- Page accessible via existing `_require_insights_access()` (`hod`, `subject_head`, `lead`, `manager`, `owner`).
- Layout & widget data: any role above.
- Goals CRUD: `hod`, `subject_head`, `lead` may mutate (subject_head scoped to their subject); `manager` and `owner` read-only.
- AI analysis generation: any role above; cached per band.

**Note on the "SH own page" follow-up.** The user asked that Subject Heads be able to set goals "on their own page." v1 reuses the existing `/department/insights` surface with the goal modal's defaults tuned for SH viewers. A dedicated SH page (e.g. `/subject/insights`) with subject-scoped band tabs and a subject-locked goals widget is a v2 addition; the schema already supports it via `DepartmentGoal.target_subject`.

## 10. Migration / backwards-compat

- Two new tables (`department_dashboard_layout`, `department_goal`). Auto-created via existing `init_db()` machinery.
- No mutations to existing tables. The current `/department/insights` view is replaced wholesale; the existing `/department/insights/data` endpoint stays alive for the deprecated `class_insights.html` deep-link.
- The Sec 4/5 collapse means classes with `level='Sec 4'` and `level='Sec 5'` share a tab. `bands.py.resolve_band` is the single source of truth — any callers that need level filtering should import it.

## 11. Failure modes & robustness

| Risk | Mitigation |
|---|---|
| Stale layout references a renamed widget key | Renderer silently skips unknown keys, keeps rest of layout intact, logs warning. |
| Heavy SQL on large schools | All metrics computed in one pass over `Submission.query_no_blobs()` per band; assignments/classes/students pre-loaded into dicts. Target: <1.5s for a 500-submission band. |
| AI hallucinates a teacher name | Client-side regex sweep + one re-prompt + banner fallback. Server-side prompt + temperature 0 lowers the risk further. |
| Concurrent layout edits across tabs | Last-write-wins per teacher (single row PK). No multi-tab requirement. |
| Empty band (no classes in band yet) | Per-widget empty states; band-level banner "No classes in this band yet" above an otherwise blank grid. |
| Term boundary not configured | All term-windowed widgets fall back to rolling 26 weeks; UI shows a one-time "Set term dates" prompt in the toolbar. |
| Subject Head's primary subject ambiguous | If the SH teaches classes covering multiple subjects, the goal modal's subject dropdown defaults to the most-frequent and surfaces the others. No automatic guess; SH must pick. |

## 12. Testing

- Unit tests for `bands.resolve_band` covering every alias in §3.2.
- Unit tests for each widget's metric function with synthetic submissions/edits.
- Integration test: hit `/department/insights/widgets?band=sec1` on a seeded demo+dept DB, assert payload shape & non-null fields for the templated widgets.
- Integration test: HOD creates a goal, widget re-fetch reflects it; teacher (not HOD) can't POST a goal; SH can POST only when target_subject is set.
- Visual smoke test (manual): each starter template renders without console errors across all 4 band tabs.
- Regex sweep test: feed the AI analysis a payload that would tempt teacher-naming; assert sweep catches & re-prompts.

## 13. Out of scope (v2+)

- Cross-cohort delta widget (this-year vs last-year same level).
- Teacher-initiated intervention requests (needs a small UI in My Class for teachers to flag students).
- Recent annotations feed (teacher-supplied known-issue notes per assignment).
- AI confidence field on the marking output (would let `partial_credit_hotspots` become a true uncertainty widget).
- Per-band score-distribution overlay with same-band-last-year (depends on cross-cohort delta infra).
- Drill-down from any widget to individual students/classes (intentionally excluded — see §1.2).
- Dedicated Subject-Head insights page (`/subject/insights`) — schema-ready, UI deferred.
