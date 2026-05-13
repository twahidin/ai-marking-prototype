# Calibration edits: intent tagging, per-assignment merge, scalable subject standards

**Status:** Draft for review
**Author(s):** Joe Tay (product), Claude (design)
**Date:** 2026-05-13
**Branch:** sandbox_upgraded

## 1. Problem

Today, when a teacher edits AI-generated feedback on a marked submission, they get a single "Save to calibration bank" checkbox. Every saved edit feeds into the same downstream pipeline:

- Tier 0 retrieval pulls it back when the same assignment is re-marked.
- Tier 1 retrieval pulls it into ANY future marking job for the same subject.
- Above 8 edits per subject, an AI synthesises them into a flat `MarkingPrinciplesCache` markdown blob that overlays every marking prompt in that subject.

Three problems compound:

1. **No distinction between blip fixes and standard updates.** A one-off AI misread ("AI thought the diagram showed a bird, it's a fish") and a genuine department-wide rule ("must say temperature, not heat") both enter the bank with equal weight. The synthesised principles file becomes diluted with noise.
2. **Flat principles file does not scale.** A real subject accumulates 500+ fine-grain standards over time. A flat document loaded into every marking prompt either gets truncated by token budget or suffers attention degradation past ~50 entries. Year-over-year transfer breaks down past a small ceiling.
3. **Edits are invisible.** Teachers can't see what AI considers "the standard". The MarkingPrinciplesCache is auto-generated and unread.

The goal: **reduce AI misreading and keep AI feedback pitched at professional teacher standards**, with explicit teacher control over what generalises and what stays local. Long-term, the system should need fewer edits over time as standards accumulate, converging toward "AI gets it right the first time" so the marking process feels easier — not harder — for teachers.

Out of scope:
- Tone/phrasing-only edits — not tracked as standards.
- A second AI marking pass — single-pass only.

## 2. High-level approach

Replace the single calibration toggle with two explicit, non-mutually-exclusive intent checkboxes. Route each intent to a different storage and retrieval mechanism:

| Intent | Storage | Retrieval scope |
|---|---|---|
| `Amend answer key` (this assignment) | `FeedbackEdit` (existing, slightly extended) | Merged into the assignment's effective answer key at prompt time |
| `Update subject standards` | New `SubjectStandard` table | Topic-scoped retrieval at marking time, capped per assignment |

If neither is ticked, only the script's marking result is updated. No row written. No propagation.

Behind these checkboxes:

- **Per-assignment answer key merge** — when previewed or sent to the marking AI, the answer key for the assignment renders as `original + teacher clarifications appended`.
- **Assignment bank decoupling** — amendments stay local to the teacher's copy. The shared bank version updates only when the teacher explicitly clicks "Update bank version with my amendments".
- **Topic-scoped subject standards** — promoted edits are AI-tagged with topic keys from a controlled vocabulary; retrieval at marking time pulls only standards whose topic overlaps the assignment's topics. Bank can hold thousands of principles; prompt window stays constant.
- **Review queue for promoted candidates** — HOD and subject leads approve / reject / edit before promotion goes live. Direct in-browser editing on the standards page.

**Additive, not gating.** The new system layers on top of today's marking flow. When subject standards exist and topic tags are available, retrieval adds them to the prompt to improve fidelity. When they don't (day-one deploys, brand-new subjects, failed tagging, no standards for the topic yet), marking proceeds exactly as it does today — students always get feedback. The system gets smarter as it accumulates data; it never breaks if data is missing.

## 3. Data model

### 3.1 Extensions to `FeedbackEdit`

| Column | Type | Purpose |
|---|---|---|
| `amend_answer_key` | bool, default false | Teacher ticked "Amend answer key" |
| `promoted_to_subject_standard_id` | int FK nullable | If teacher ticked "Update subject standards", points to the created/reinforced SubjectStandard row |

Existing `active` continues to mean "this edit is part of the calibration set". `scope` repurposed to one of: `'amendment'` (assignment-scoped only), `'promoted'` (also fed into subject standards), or `'both'`.

### 3.2 New table: `SubjectStandard`

```python
class SubjectStandard(Base):
    id: int (PK)
    uuid: str (unique, generated at insert)        # stable external ID for KB exports
    subject: str (FK to canonical subject key)
    text: str                                       # the principle, 50–250 chars
    topic_keys: JSON list                           # 1–3 keys from controlled vocab; [] if untagged
    theme_key: str | None                           # inherited from source FeedbackEdit
    reinforcement_count: int, default 1
    status: enum('pending_review','active','archived'), default 'pending_review'
    created_by: int FK Teacher
    created_at: datetime
    updated_at: datetime                            # bumps on any change
    last_seen_at: datetime                          # bumps on reinforcement only (kept for export, not ranking)
    reviewed_by: int FK Teacher, nullable           # set on initial approval
    reviewed_at: datetime, nullable
    source_feedback_edit_ids: JSON list             # audit chain
    metadata: JSON object, default {}               # forward-compatible
```

Indexes: `(subject, status)`, JSON containment on `topic_keys`.

### 3.3 New table: `SubjectTopicVocabulary`

```python
class SubjectTopicVocabulary(Base):
    subject: str
    topic_key: str          # snake_case slug
    display_name: str
    active: bool, default true
    PRIMARY KEY (subject, topic_key)
```

Seeded from `config/subject_topics/<subject>.py` at first boot. Mutable thereafter via UI by HOD / subject lead.

### 3.4 New permission table: `SubjectLead`

```python
class SubjectLead(Base):
    teacher_id: int FK
    subject: str
    PRIMARY KEY (teacher_id, subject)
```

A teacher can be lead for one or more subjects. HOD role implicitly has subject-lead rights for all subjects. Subject head and subject lead roles are treated as one permission tier ("can edit subject standards for X").

### 3.5 Extensions to `Assignment`

| Column | Type | Purpose |
|---|---|---|
| `topic_keys` | JSON list | Per-question topic tags: `[[topic_keys_q1], [topic_keys_q2], ...]` |
| `topic_keys_status` | enum('legacy','pending','tagged') | Tagging lifecycle. `legacy` is set on every assignment that exists at deploy time; these are never AI-tagged and never participate in the new system. New assignments start `pending` and transition to `tagged` once topic extraction completes. See §4.4 and §7. |
| `bank_pushed_at` | datetime, nullable | When local version was last pushed to bank (used for concurrency check) |

## 4. User flows

### 4.1 Teacher edits feedback on a marked script

Existing inline-edit UI gains two checkboxes (replacing today's single "Save to calibration bank"):

```
┌─ Edit feedback ──────────────────────────────────────┐
│ [textarea — teacher's edited feedback]               │
│                                                      │
│ [ ] Amend answer key for this assignment             │
│ [ ] Update subject standards                         │
│                                                      │
│                       [ Cancel ]  [ Save ]           │
└──────────────────────────────────────────────────────┘
```

Routing on save:

- Both unchecked → submission's `result_json` updated; no `FeedbackEdit` row; no `SubjectStandard` change.
- `Amend answer key` only → `FeedbackEdit` row with `amend_answer_key=true`, `scope='amendment'`.
- `Update subject standards` only → `FeedbackEdit` row with `amend_answer_key=false`, `scope='promoted'`. AI tagging job kicks off; SubjectStandard row created (`pending_review`) or existing one reinforced.
- Both checked → `FeedbackEdit` row with `amend_answer_key=true`, `scope='both'`. AI tagging job kicks off; SubjectStandard row created or reinforced.

The "Update subject standards" checkbox is disabled for freeform subjects (not in canonical list), with hover text: "Choose a canonical subject for this assignment to enable subject-wide standards."

**On legacy assignments** (`topic_keys_status='legacy'` — created before this design rolled out), the "Update subject standards" checkbox is hidden entirely. Legacy assignments never get topic tags, so subject-standard retrieval can't target them, and promotions from them would only pollute the new bank with edits not anchored to today's vocabulary. "Amend answer key" remains available so teachers can still scope new edits to the whole assignment.

### 4.2 Previewing the answer key for an assignment

The assignment page renders the **effective answer key**: original uploaded content, followed by a clearly demarcated "Teacher clarifications" section appended at the bottom:

```
── Teacher clarifications (added since upload) ──

Q3: Accept "powerhouse of the cell" as equivalent to mitochondria.
    Added by Joe Tay, 2026-05-12.

Q5: AI keeps misreading the diagram on Q5 — the species drawn is a fish, not a bird.
    Added by Joe Tay, 2026-05-10.
```

Each clarification has a small edit + remove action visible to its author (and HOD). The full merged form is what the AI sees during marking.

### 4.3 Pushing amendments to the assignment bank

If the assignment is currently in the assignment bank, a new button appears next to "Share to assignment bank":

```
[ Update bank version with my amendments ]
```

- Disabled if no amendments since the last push, OR if the assignment isn't in the bank.
- On click, modal shows the diff and warns about concurrent writes:
  ```
  You have 3 amendments not in the bank version:
   • Q3: Accept "powerhouse of the cell" (added 2026-05-12)
   • Q5: Diagram is a fish, not a bird (added 2026-05-10)
   • Q7: Accept temperatures in °C or K (added 2026-05-09)

  Bank version was last updated by Jane Lim on 2026-04-22.
  Your local version is 3 amendments ahead.

  [ Cancel ]  [ Push to bank ]
  ```
- **Optimistic concurrency:** the push request includes the `bank_pushed_at` timestamp the client last saw. If the server's current value is newer (another teacher pushed in between), the server returns 409. The modal re-renders with: "Bank was updated by [name] on [date], AFTER you pulled. Pushing will overwrite their changes." Teacher must explicitly confirm a second time.

### 4.4 Topic tagging at assignment creation

When a teacher saves a new assignment (uploads question paper + answer key):

1. Background job runs per-question topic extraction (Haiku, controlled vocab from `SubjectTopicVocabulary`). Typical latency 5–15 seconds.
2. **Marking is never blocked by tagging.** Teacher can hit "Mark scripts" immediately. If tagging hasn't completed by the time the marking job assembles its prompt, subject-standard retrieval is skipped for that batch — marking proceeds with answer key + amendments only, identical to today's behaviour. The next marking action picks up the tags once they arrive.
3. On AI tagger failure: retry up to 3 times with exponential backoff. If all retries fail, `topic_keys = []` and the assignment is flagged with a small "needs manual tagging" badge on the assignment page. Marking continues to work normally (without subject-standard retrieval) — the teacher can add tags manually any time via the editable Topic tags section to enable retrieval for future marking jobs.
4. Job writes `Assignment.topic_keys` and sets `topic_keys_status = 'tagged'`.
5. Assignment page shows a Topic tags section as a comma-separated, click-to-edit text field per question:
   ```
   Topic tags
     Q1  enzymes, terminology_precision        [edit]
     Q2  cellular_respiration                  [edit]
     Q3  photosynthesis, diagram_labelling     [edit]
   ```
6. Editing opens a text field; teacher types comma-separated topic keys. Free-form, no chip-typeahead in MVP. Server-side validates against `SubjectTopicVocabulary` and discards unknown keys silently (written to application log only — no separate review surface in MVP; subject leads add new topic keys through the standards-page admin section).

### 4.5 Marking-time retrieval

When `mark_script()` is called:

1. Read `Assignment.topic_keys` (per question).
2. For each question `q_i`:
   - Query `SubjectStandard WHERE subject = X AND status = 'active' AND topic_keys ∩ q_i.topic_keys != ∅`.
   - Rank: `reinforcement_count DESC`.
   - Per-question quota: top 3.
3. Dedup across questions.
4. Apply absolute ceiling: **30 standards per assignment**, regardless of question count.
5. Read amendments from `FeedbackEdit WHERE assignment_id = X AND active = true AND amend_answer_key = true`.
6. Assemble prompt:
   ```
   [Question paper]
   [Answer key]

   ── Teacher clarifications for this assignment ──
   Q3: Accept "powerhouse of the cell" ...
   Q5: AI keeps misreading the diagram — it's a fish ...

   ── Subject standards relevant to this assignment ──
   For enzymes / terminology_precision:
     - Accept "temperature" but reject "heat" — heat is energy ...
     - Reject "fast" / "slow" without quantifier ...
   For cellular_respiration:
     - ...
   ```
7. Submit to AI as before.

**Fallback to today's behaviour.** If `Assignment.topic_keys` is empty (tagging hasn't run or failed), if the topic-overlap query returns zero matching active standards, or if there are no amendments to inject, the corresponding sections are simply omitted from the prompt. Marking always proceeds — students always get feedback. On day-one deploys with no standards bank, the prompt is identical to today's.

### 4.6 Subject standards review queue + direct edit

New page in Settings: "Subject standards" (visible to HOD and subject leads for their subjects).

**Pending review section** (top):
```
Pending review (4)
  Biology — enzymes, terminology_precision
    "Accept 'temperature' but reject 'heat' — heat is energy ..."
    Reinforced 3× • Sources: 3 edits by Joe Tay

    Related existing standards on this topic:
      • "Use SI units (Kelvin) on thermal physics — not °C"
        (reinforced 8×, active)
      • "Don't accept 'thermal energy' as a synonym for heat in
         calorimetry questions" (reinforced 4×, active)

    [ Approve ]  [ Edit ]  [ Reject ]
```

The "Related existing standards" panel lists up to 5 active standards from the same subject with overlapping topic_keys, so the reviewer can spot contradictions or near-duplicates before approving. Pulled at page-load by the same retrieval query used for marking.

**Active standards section**, grouped by subject → topic:
```
Biology — Subject standards (87 active)

  ▼ Enzymes (12 standards)
      "Accept 'temperature' but reject 'heat' ..."
        Reinforced 12×                          [edit] [archive]
      "Reject 'fast' / 'slow' without quantifier"
        Reinforced 7×                           [edit] [archive]
      ...
  ▼ Cellular respiration (8 standards)
      ...
```

Each standard's text is inline-editable: click the text → textarea → save. Edit bumps `updated_at`, `reviewed_by`, `reviewed_at`.

Filters: subject, topic, status, search by text.

### 4.7 Promotion lifecycle

```
Teacher edits feedback, ticks "Update subject standards"
        │
        ▼
Server writes FeedbackEdit row, scope='promoted' or 'both'
        │
        ▼
Async job: AI tags edit with topic_keys (Haiku, ~$0.0001)
  ├─ Success → continue
  └─ 3 retries fail → row tagged topic_keys=[], flagged in queue
        │
        ▼
Server checks for similar SubjectStandard:
  (subject, topic_keys overlap, text cosine similarity > 0.85)
        │
        ├─ Match found ────▶  Reinforce: count++, append source id,
        │                     last_seen_at = now, updated_at = now.
        │                     No status change. No re-review needed.
        │
        └─ No match ──────▶  Insert SubjectStandard:
                              status='pending_review',
                              reinforcement_count=1.
                              Appears in HOD/subject-lead queue with
                              "Related existing standards" panel.
```

Approved → `status='active'`. Active standards enter retrieval.

### 4.8 Rubric / answer key re-upload

When a teacher re-uploads the rubric PDF or answer key on an existing assignment:

1. A new `rubric_version` is generated.
2. All existing amendments (FeedbackEdits with `amend_answer_key=true`) tied to the old `rubric_version` are **automatically re-pinned** to the new `rubric_version`.
3. A banner appears on the assignment page: `N amendments carried over from previous rubric. [Review them]`.
4. Clicking "Review them" opens the amendments list with a one-click archive action per row, so the teacher can prune any that no longer apply.

This handles the common case (typo fix, minor edit) with zero friction while still letting the teacher clean up if the rubric changed substantially.

### 4.9 Hide the teacher theme-key correction UI

Today the teacher's result view shows an inline category dropdown on each criterion (rendered in `static/js/feedback_render.js` around the `renderTriggerInner` / theme-pill flow). It lets the teacher override the AI-assigned `theme_key`. In practice, the categorisation has been accurate enough that teachers rarely use this surface — and the new intent checkboxes (§4.1) take precedence as the teacher's actionable surface on the result view.

**Hide the teacher theme-key dropdown by default.**

- Gate the rendering on a new env var `TEACHER_THEME_UI_ENABLED`, defaulting to `FALSE`. This mirrors the existing `STUDENT_GROUPING_UI_ENABLED` pattern.
- The underlying categorisation pipeline continues to run unchanged: `theme_key` is still populated on each criterion by Pass 2 categorisation, still inherited by `FeedbackEdit` at save time, still attached to promoted `SubjectStandard` rows.
- The `CategorisationCorrection` write path is dormant while the UI is hidden (no inline override → no correction row). The few-shot examples it feeds in the categorisation prompt go static, which is fine given accuracy is already good.
- Re-enabling later (if accuracy drops) is a single env-var flip — no code change, no data migration.



`config/subject_topics/<subject>.py` per canonical subject. Example (`biology.py`):

```python
TOPICS = [
    ("enzymes", "Enzymes"),
    ("cellular_respiration", "Cellular respiration"),
    ("photosynthesis", "Photosynthesis"),
    ("genetics", "Genetics"),
    ("circulatory_system", "Circulatory system"),
    # ...
    # Cross-cutting (apply across content topics)
    ("terminology_precision", "Terminology precision"),
    ("units", "Units"),
    ("diagram_labelling", "Diagram labelling"),
    ("calculation", "Calculation"),
    ("experimental_method", "Experimental method"),
]
```

On first boot, seed `SubjectTopicVocabulary` from these files. Subject lead can add/disable through the UI.

If AI tagging proposes a key not in the vocabulary, it's silently discarded (logged for subject lead awareness). Subject leads add new topic keys directly through the admin UI on the standards page rather than through a separate "proposed new topic" review flow.

## 6. Export

Endpoint: `GET /api/subject_standards/export?subject=biology&format=jsonl&updated_since=2026-01-01`

Permissions: HOD / subject lead of the requested subject.

Response: streamed JSONL. Each line:

```json
{
  "id": "ss_b1f7a2e0-...",
  "content": "Accept 'temperature' but reject 'heat' — heat is energy, temperature is the parameter.",
  "metadata": {
    "subject_key": "biology",
    "subject_display": "Biology",
    "topic_keys": ["enzymes", "terminology_precision"],
    "theme_key": "terminology_precision",
    "reinforcement_count": 12,
    "status": "active",
    "created_at": "2026-04-02T08:13:00Z",
    "updated_at": "2026-05-10T14:22:00Z",
    "created_by": {"name": "Joe Tay", "role": "subject_lead"},
    "reviewed_by": {"name": "Jane Lim", "role": "hod"}
  }
}
```

Compatible without transformation with Pinecone, Chroma, Weaviate, Qdrant, Bedrock Knowledge Bases, pgvector.

## 7. Migration (clean break for old work, in-flight work preserved)

**Existing data is deprecated on deploy, except for the most recent in-flight work.** The subject-standards bank always starts empty (built fresh from deliberate post-deploy promotions). Recent assignments (within 5 days of deploy) are treated as "in-flight" and onboarded into the new system; older assignments become legacy and are visually unchanged.

The 5-day cutoff is intentional: schools typically have a handful of assignments actively being marked at any moment. Forcing those to start over would destroy in-progress calibration work. Older assignments are already done or near-done, so a clean-slate is acceptable.

On deploy:

1. **Schema migration** — add new columns/tables via `_migrate_add_columns` in `db.py`.
2. **Vocab seed** — populate `SubjectTopicVocabulary` from `config/subject_topics/*.py`.
3. **Classify existing assignments by age:**
   - Assignments with `created_at` within 5 days of deploy time → set `topic_keys_status='pending'`. Lazy AI tagging will run on first open after deploy (typical cost ~$0.001 each × handful of assignments = a few cents total). Non-blocking — marking proceeds with today's behaviour until tags arrive.
   - Assignments older than 5 days → set `topic_keys_status='legacy'`. Never AI-tagged. Visually unchanged.
4. **Reclassify existing `FeedbackEdit` rows by their parent assignment's age:**
   - Edits on in-flight assignments (≤5 days) → keep `active=true`, set `amend_answer_key=true`, `scope='amendment'`. They behave as new-system amendments, merging into the effective answer key on subsequent re-marks. They do **not** auto-promote to SubjectStandards — promotion requires an explicit teacher action with the new checkbox.
   - Edits on legacy assignments (>5 days) → set `active=false`. They no longer affect marking. Rows preserved for historical audit; existing UI surfaces that already render them on past submission pages continue to do so as historical context.
5. **Deactivate `MarkingPrinciplesCache`** — stop regenerating, stop applying, regardless of assignment age. Table preserved for audit. The standards bank starts empty for every subject.
6. **All new assignments (created after deploy) and all new edits (saved after deploy) use the new system from the moment of deploy.**

Behaviour on legacy assignments after deploy:
- Re-marking a script no longer pulls in old FeedbackEdits or `MarkingPrinciplesCache`. Marking is performed against the original answer key as uploaded.
- Teachers can still edit feedback on individual submissions (today's per-submission update behaviour, no checkbox ticked).
- Teachers can optionally tick **"Amend answer key"** to scope a new edit to the whole legacy assignment — this writes a fresh `FeedbackEdit` row using the new system and applies on subsequent re-marks of that assignment.
- **"Update subject standards" is hidden** on legacy assignments — they don't have topic tags and can't contribute to the new bank.

Behaviour on in-flight assignments (≤5 days at deploy) after deploy:
- Topic tagging runs lazily on first open (typically 5–15s, non-blocking).
- Existing pre-deploy FeedbackEdits remain active as Tier-0 amendments — they continue to apply on re-marks.
- Once tagged, the assignment is treated as a new-system assignment: both intent checkboxes are available, retrieval pulls relevant subject standards (when any exist in the bank), and so on.

Behaviour on new assignments (created after deploy):
- Full new system applies as designed in §3–§6.

Teachers see no banner, no migration prompt, no pending-review queue noise on day one. The standards bank fills up organically as deliberate, post-deploy work generates promoted standards.

## 8. Cost model

| Event | Frequency | Cost per event |
|---|---|---|
| Topic tagging at assignment creation (or first open after migration) | Once per assignment | ~$0.001 (~10 questions × Haiku) |
| Topic tagging on subject-standard promotion | Once per promoted edit | ~$0.0001 |
| Per-script marking with retrieval injected | Per script | +~$0.005 (vs ~$0.012 baseline) |
| Class of 40 total marking cost | Per class | ~$0.46 → ~$0.52 (+$0.06) |
| One-time migration | At deploy | **~$0.01–$0.05** (lazy tagging of in-flight assignments only — handful of assignments × $0.001 each) |

Marking cost stays effectively flat in subject-standard count — the per-topic quota + 30-standard ceiling bound the prompt size regardless of how big the standards bank grows.

## 9. Edge case handling

| Edge case | Behaviour |
|---|---|
| Solo-teacher deployment (`DEPT_MODE=FALSE`) | The sole teacher has implicit subject-lead permission for all subjects. Promotions auto-activate (no `pending_review` step) since there's no separate reviewer. |
| Demo mode (`DEMO_MODE=TRUE`) | Subject standards pipeline is disabled entirely. Two intent checkboxes still appear but "Update subject standards" is hidden. Amendments work (session-only, like other demo-mode data). |
| AI tagger fails after 3 retries (assignment tagging) | `topic_keys = []`, assignment shows "needs manual tagging" badge. **Marking is never blocked** — subject-standard retrieval is skipped and marking falls back to today's behaviour. Teacher can manually tag any time. |
| AI tagger fails after 3 retries (subject-standard promotion) | `topic_keys = []` on the SubjectStandard candidate, flagged "needs manual tagging" in review queue. Subject lead adds tags manually before approving. |
| Empty `Assignment.topic_keys` at marking time | Subject-standard retrieval skipped for that marking batch. Marking proceeds normally with answer key + amendments. The next batch benefits once tagging completes. |
| Zero subject standards match the assignment's topics (day-one deploys, niche topics) | Identical to today's behaviour — marking proceeds with answer key + amendments only. No error, no warning, no degraded student experience. |
| Bank push race | Optimistic concurrency on `bank_pushed_at`; 409 returns the conflict to the teacher who must confirm overwrite explicitly. |
| Rubric re-upload | Existing amendments auto-carry to new `rubric_version`. Banner offers one-click review of carried amendments. |
| Contradictory standards from different teachers | At approval time, the review queue surfaces a "Related existing standards" panel listing active standards on overlapping topics. The reviewer (HOD / subject lead) sees contradictions and decides to reject or edit before approving. |
| Freeform subject assignments | Cannot promote to subject standards (checkbox disabled). Amendments still work normally. |
| Legacy assignments (created >5 days before deploy) | `topic_keys_status='legacy'`. Visually unchanged from today. Old FeedbackEdits on them are deactivated and no longer affect marking. "Amend answer key" available; "Update subject standards" hidden. Re-marking uses the original answer key only (any post-deploy amendments merge in normally). |
| In-flight assignments (created within 5 days before deploy) | `topic_keys_status='pending'`. AI tagging runs lazily on first open. Pre-deploy FeedbackEdits stay active as Tier-0 amendments. Both intent checkboxes become available once tagging completes. |
| Assignment subject changed after promotion | The previously-promoted SubjectStandard stays under the original subject (it's attached to that subject's bank, not the assignment). Teachers can archive via the standards page if it became irrelevant. |
| Pinned principles | Deferred to v2. Not in MVP. |

## 10. Testing

- **Unit tests:** schema migrations, retrieval query (topic overlap + ranking + cap), AI tagger response parsing, AI tagger retry/failure behaviour, prompt assembly with amendments + standards, optimistic concurrency on bank push, rubric re-upload carry-over.
- **Integration tests:** end-to-end edit save with both intents, AI tagger mocked, retrieval injection visible in assembled prompt, bank update flow with concurrent-write 409, rubric re-upload banner flow.
- **Permission tests:** non-HOD non-subject-lead cannot access standards page or export. Solo-teacher mode auto-approves. Demo mode hides "Update subject standards".
- **Migration tests:** boot with pre-migration data, verify lazy topic tagging on first assignment open, legacy `MarkingPrinciplesCache` still applies for old edits during the cutover window.

## 11. v2 backlog (explicitly deferred)

- **Pinning** — subject lead can force a standard into every retrieval regardless of topic match.
- **"Merge into…" action** in review queue — combine two pending candidates into a single SubjectStandard.
- **Chip-typeahead UI** for per-question topic tags (MVP uses plain comma-separated text).
- **AI confidence display** on auto-tagged topics.
- **Cumulative-paper bonus cap** — extend the 30-cap to 40 for assignments tagged "cumulative".
- **English-lit / comprehension specific tuning** — skill-style topics instead of content topics for subjects where content vocab doesn't fit.
- **`supporting_examples`** array in JSONL export — bundle source FeedbackEdit text + question text with each principle.
- **Subject standard versioning** beyond `updated_at` — track full edit history.
- **Embedding-based dedup** at promotion time instead of cosine similarity on raw text.
- **Cross-subject linkage** — a chemistry question in a biology paper retrieving from both subjects.
- **HOD bulk actions** beyond row-by-row — export selected, archive selected.
