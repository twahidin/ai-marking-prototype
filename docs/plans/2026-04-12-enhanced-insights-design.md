# Enhanced Insights — AI Analysis & Item Analysis

## Goal

Add AI-powered general sensing and cross-assignment item analysis to the Department Insights page. HOD can generate narrative summaries with action items, and compare per-question performance across multiple assignments.

## Decisions

- **AI analysis**: On-demand with persistence. HOD clicks "Generate Analysis", result is saved and shown on revisit.
- **Provider selection**: HOD picks provider/model before generating. Default to cheapest available (Haiku > GPT Mini > Qwen).
- **Item analysis matching**: Manual — HOD selects 2+ assignments to compare side-by-side.
- **AI output format**: Summary paragraph + action items bullet list.

## Feature 1: AI General Sensing

### UI

- New card below the existing charts: "AI Analysis"
- Contains:
  - Provider/model selector (all available providers from env + dept keys, default cheapest)
  - "Generate Analysis" button
  - Response area: **Summary** paragraph + **Action Items** bullets
  - If saved analysis exists for current filters, show it with "Regenerate" button
  - Loading state while AI call is in progress

### Backend

- New endpoint: `POST /department/insights/analyze`
  - Accepts: `assignment_id`, `class_id` (filters), `provider`, `model`
  - Collects the same data as `/department/insights/data` (class scores, question difficulty, per-student breakdown)
  - Builds a structured text prompt with the data
  - Calls AI provider (text-only, no vision)
  - Parses response into `{summary, action_items}`
  - Saves to `DepartmentConfig` keyed as `insight_analysis:{assignment_id or 'all'}:{class_id or 'all'}`
  - Returns JSON response

- New endpoint: `GET /department/insights/analysis`
  - Accepts: `assignment_id`, `class_id` (filters)
  - Returns saved analysis if exists, else `{exists: false}`

### Prompt Design

Send to AI:
- Number of students, classes, assignments in scope
- Per-class average scores
- Score distribution (0-20, 21-40, etc.)
- Per-question difficulty (% correct)
- Bottom 5 students with their scores and weak areas
- If item analysis data is available, include cross-assignment comparison

Ask for:
1. A 2-3 sentence summary of overall performance
2. 3-5 specific, actionable recommendations (identify struggling groups, topics needing review, bright spots)

### Storage

Use existing `DepartmentConfig` model (key-value store). Key format: `insight_analysis:{asn_id}:{cls_id}`. Value: JSON with `{summary, action_items, provider, model, generated_at}`.

## Feature 2: Item Analysis (Cross-Assignment Comparison)

### UI

- New card at bottom: "Item Analysis — Compare Assignments"
- Assignment selector: checkboxes grouped by class, showing assignment title + class name
- "Compare" button (disabled until 2+ selected)
- Results table:
  - Rows = question numbers
  - Columns = selected assignments (header: class name — assignment title)
  - Cells = % correct, color-coded (green >= 70, yellow >= 50, red < 50)
- Empty state: "Select 2 or more assignments to compare per-question performance"

### Backend

- New endpoint: `GET /department/insights/item-analysis`
  - Accepts: `assignment_ids` (comma-separated list)
  - For each assignment, loads all done submissions, computes per-question % correct
  - Returns: `{assignments: [{id, title, class_name, questions: {qnum: pct}}], question_numbers: [...]}`

### Data Flow

1. HOD checks 2+ assignments from the list
2. Clicks "Compare"
3. Frontend calls `/department/insights/item-analysis?assignment_ids=id1,id2,id3`
4. Backend returns per-question stats per assignment
5. Frontend renders comparison table
6. If HOD then clicks "Generate AI Analysis", the item analysis data is included in the AI prompt

## Files to Modify

- `templates/department_insights.html` — Add AI analysis card, item analysis card, JS
- `app.py` — Add 3 new endpoints (analyze, get analysis, item-analysis)
- No model changes needed (uses existing DepartmentConfig for storage)

## Out of Scope

- Auto-matching assignments by title
- Streaming AI responses
- Per-student drill-down from insights
