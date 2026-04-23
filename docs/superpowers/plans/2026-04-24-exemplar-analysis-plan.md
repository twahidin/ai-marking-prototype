# Exemplar Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an AI-powered Exemplar Analysis page that surfaces 3–8 "areas for discussion" per assignment, each backed by 2 "needs work" and 2 "strong" student exemplars rendered as full script pages via the existing DocumentViewer.

**Architecture:** One DB migration (two columns on `Assignment`). One AI helper in `ai_marking.py` + three Flask routes in `app.py` (render page, generate + cache analysis, fetch cached). One new template `templates/exemplars.html` that reuses the shared `DocumentViewer` module for tile rendering. One button link added to `teacher_detail.html`.

**Tech Stack:** Flask, SQLAlchemy, Jinja2, vanilla JS, PDF.js via the existing `document_viewer.js`. No new Python deps.

**Spec:** `docs/superpowers/specs/2026-04-23-exemplar-analysis-design.md`

---

## Task 1: DB schema — add exemplar columns to Assignment

**Files:**
- Modify: `db.py` (column definitions + auto-migration)

- [ ] **Step 1: Add the column definitions**

In `db.py`, find the `Assignment` class definition. Add after the `needs_remark` column:

```python
    exemplar_analysis_json = db.Column(db.Text)
    exemplar_analyzed_at = db.Column(db.DateTime)
```

- [ ] **Step 2: Add auto-migration for the new columns**

In `db.py`, find the block that adds `needs_remark` via `ALTER TABLE assignments ADD COLUMN needs_remark ...`. Immediately after that block, add:

```python
            if 'exemplar_analysis_json' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN exemplar_analysis_json TEXT'))
                db.session.commit()
                logger.info('Added exemplar_analysis_json column to assignments table')
            if 'exemplar_analyzed_at' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN exemplar_analyzed_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added exemplar_analyzed_at column to assignments table')
```

Match the existing code's exact indentation by looking at the surrounding block.

- [ ] **Step 3: Compile check**

```bash
python3 -m py_compile db.py
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add db.py
git commit -m "feat(db): add exemplar_analysis_json + exemplar_analyzed_at columns on Assignment"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 2: AI analysis helper

**Files:**
- Modify: `ai_marking.py` (add a new function)

- [ ] **Step 1: Add `generate_exemplar_analysis` helper**

Append this function to `ai_marking.py` (append at end of file, before any `if __name__ == '__main__'` blocks if present, otherwise just at EOF):

```python
def generate_exemplar_analysis(provider, model, session_keys, subject, submissions_data):
    """Run AI exemplar analysis across all done submissions for a class.

    submissions_data: list of dicts, one per done submission, each with:
        - submission_id (int)
        - student_name (str)
        - marks_awarded (int or None)
        - marks_total (int or None)
        - questions: list of {question_num, student_answer, correct_answer, feedback, improvement, status}
        - overall_feedback (str)
        - page_count (int)

    Returns: {"areas": [{question_part, label, description,
              needs_work_examples:[{submission_id, page_index, note}],
              strong_examples:[{submission_id, page_index, note}]}, ...]}

    Raises an Exception on API/parse failure; the caller translates to HTTP.
    """
    import json as _json

    # Build a compact textual representation to fit the prompt.
    lines = [f"Subject: {subject}", f"Total students: {len(submissions_data)}", ""]
    for s in submissions_data:
        score = ''
        if s.get('marks_awarded') is not None and s.get('marks_total') is not None:
            score = f" ({s['marks_awarded']}/{s['marks_total']})"
        lines.append(f"--- Student (submission_id={s['submission_id']}){score} | pages={s['page_count']} ---")
        for q in s.get('questions') or []:
            qn = q.get('question_num') or '?'
            ans = (q.get('student_answer') or '').strip().replace('\n', ' ')
            fb = (q.get('feedback') or '').strip().replace('\n', ' ')
            if len(ans) > 400:
                ans = ans[:400] + '…'
            if len(fb) > 300:
                fb = fb[:300] + '…'
            lines.append(f"Q{qn}: student_answer: {ans}")
            if fb:
                lines.append(f"Q{qn}: feedback: {fb}")
        if s.get('overall_feedback'):
            of = s['overall_feedback'].strip().replace('\n', ' ')
            if len(of) > 300:
                of = of[:300] + '…'
            lines.append(f"overall_feedback: {of}")
        lines.append("")
    user_prompt = "\n".join(lines)

    system_prompt = (
        "You are an education analytics assistant preparing exemplars for a post-marking class discussion.\n\n"
        "You will receive every student's answers and AI feedback for a class's assignment. Produce a short JSON list of 'areas for analysis'. "
        "Each area should be:\n"
        "- Tied to a SPECIFIC, CONCRETE issue observed in the ACTUAL submissions — not a generic textbook category. "
        "Label it so a teacher scanning a grid of buttons can tell what the area is about at a glance. "
        "Example good labels: 'Used weight instead of mass in F=ma', 'Missed the word \"except\" in Q3', 'Weak topic sentence in intro'. "
        "Example bad labels (too generic): 'Misconception about force', 'Question-answering technique', 'Paragraph structure'.\n"
        "- Cross-subject: include question-answering technique issues (misread the question, missed keywords, "
        "didn't quote evidence, answered a different question, ignored mark allocation) alongside conceptual misconceptions, "
        "procedural errors, presentation issues, and argumentation issues, as appropriate to the subject.\n"
        "- Accompanied by FOUR concrete exemplars: TWO students whose work illustrates the issue (needs_work_examples) "
        "and TWO whose work handles it well (strong_examples). For each exemplar give submission_id (integer, must match one of the "
        "students above), page_index (0-based integer, must be < that student's page_count), and a short note pointing to where on the page to look.\n\n"
        "Return 3–8 areas, ordered by teaching value (most discussion-worthy first). Exemplars within one area should be four DIFFERENT students.\n\n"
        "Respond ONLY with valid JSON in this exact shape:\n"
        '{"areas":[{"question_part":"...","label":"...","description":"...",'
        '"needs_work_examples":[{"submission_id":0,"page_index":0,"note":"..."},{"submission_id":0,"page_index":0,"note":"..."}],'
        '"strong_examples":[{"submission_id":0,"page_index":0,"note":"..."},{"submission_id":0,"page_index":0,"note":"..."}]'
        '}]}'
    )

    prov_cfg = PROVIDERS.get(provider)
    if not prov_cfg:
        raise ValueError(f"Unknown provider: {provider}")
    prov_type = prov_cfg['type']

    api_key = session_keys.get(provider) if session_keys else None
    if not api_key:
        env_name = PROVIDER_KEY_MAP.get(provider)
        if env_name:
            api_key = os.getenv(env_name)
    if not api_key:
        raise ValueError(f"No API key configured for provider: {provider}")

    if prov_type == 'anthropic':
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        text = resp.content[0].text
    else:
        if not OPENAI_AVAILABLE:
            raise RuntimeError("OpenAI SDK not installed")
        base_url = prov_cfg.get('base_url')
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        kwargs = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        }
        if model.startswith('gpt-5') or 'gpt-5' in model:
            kwargs['max_completion_tokens'] = 4096
        else:
            kwargs['max_tokens'] = 4096
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content

    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        raise ValueError("AI response contained no JSON object")
    parsed = _json.loads(match.group())
    if 'areas' not in parsed or not isinstance(parsed['areas'], list):
        raise ValueError("AI response missing 'areas' list")
    return parsed
```

Note: references `PROVIDERS`, `PROVIDER_KEY_MAP`, `Anthropic`, `OpenAI`, `OPENAI_AVAILABLE`, `re`, `os` — all already imported at the top of `ai_marking.py`. The `json` import is aliased local to avoid shadowing any module-level usage.

- [ ] **Step 2: Compile check**

```bash
python3 -m py_compile ai_marking.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add ai_marking.py
git commit -m "feat(ai): add generate_exemplar_analysis helper"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 3: Backend routes

**Files:**
- Modify: `app.py` (three new routes near the other teacher routes, import the AI helper)

- [ ] **Step 1: Add import for the new helper**

In `app.py`, find the existing line `from ai_marking import mark_script, PROVIDERS, PROVIDER_KEY_MAP` (or similar import from `ai_marking`). Change it to include `generate_exemplar_analysis`. For example if current is:

```python
from ai_marking import mark_script, PROVIDERS, PROVIDER_KEY_MAP
```

change to:

```python
from ai_marking import mark_script, PROVIDERS, PROVIDER_KEY_MAP, generate_exemplar_analysis
```

If the exact line differs, search for `from ai_marking import` and add `generate_exemplar_analysis` to whichever existing import line already pulls from that module. Do not duplicate.

- [ ] **Step 2: Add the page-render route**

In `app.py`, find the `teacher_overview` route (search for `@app.route('/teacher/assignment/<assignment_id>/overview')`). Immediately after its closing block, add:

```python
@app.route('/teacher/assignment/<assignment_id>/exemplars')
def teacher_exemplars_page(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    total = Student.query.filter_by(class_id=asn.class_id).count()
    done_count = Submission.query.filter_by(
        assignment_id=assignment_id, status='done', is_final=True,
    ).count()
    gate_pct = int((done_count / total) * 100) if total > 0 else 0
    can_generate = total > 0 and gate_pct >= 20

    analysis = None
    student_names = {}
    if asn.exemplar_analysis_json:
        try:
            analysis = json.loads(asn.exemplar_analysis_json)
        except Exception:
            analysis = None
    if analysis and isinstance(analysis.get('areas'), list):
        ids = set()
        for area in analysis['areas']:
            for key in ('needs_work_examples', 'strong_examples'):
                for ex in area.get(key) or []:
                    if isinstance(ex.get('submission_id'), int):
                        ids.add(ex['submission_id'])
        if ids:
            rows = (
                db.session.query(Submission, Student)
                .join(Student, Submission.student_id == Student.id)
                .filter(Submission.id.in_(ids))
                .all()
            )
            student_names = {sub.id: st.name for (sub, st) in rows}

    return render_template(
        'exemplars.html',
        assignment=asn,
        total_students=total,
        done_count=done_count,
        gate_pct=gate_pct,
        can_generate=can_generate,
        analysis=analysis,
        analyzed_at=asn.exemplar_analyzed_at,
        student_names=student_names,
    )
```

- [ ] **Step 3: Add the generate route**

Immediately after `teacher_exemplars_page`, add:

```python
@app.route('/teacher/assignment/<assignment_id>/exemplars/generate', methods=['POST'])
def teacher_exemplars_generate(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    total = Student.query.filter_by(class_id=asn.class_id).count()
    done_subs = (
        Submission.query
        .filter_by(assignment_id=assignment_id, status='done', is_final=True)
        .all()
    )
    if total == 0 or (len(done_subs) / total) * 100 < 20:
        return jsonify({'success': False, 'error': 'At least 20% of the class must have done submissions.'}), 400

    # Cap to 40 submissions, sampled evenly across mark buckets if we have more.
    MAX_SUBS = 40
    selected = done_subs
    if len(done_subs) > MAX_SUBS:
        def _score(sub):
            r = sub.get_result() or {}
            qs = r.get('questions') or []
            awarded = sum((q.get('marks_awarded') or 0) for q in qs)
            total_m = sum((q.get('marks_total') or 0) for q in qs)
            return (awarded / total_m) if total_m > 0 else 0.5
        scored = sorted(done_subs, key=_score)
        step = len(scored) / MAX_SUBS
        selected = [scored[int(i * step)] for i in range(MAX_SUBS)]

    # Build per-submission payload for the AI.
    student_by_id = {
        st.id: st.name for st in Student.query.filter_by(class_id=asn.class_id).all()
    }
    submissions_data = []
    valid_subs = {}
    for sub in selected:
        result = sub.get_result() or {}
        pages = sub.get_script_pages() or []
        submissions_data.append({
            'submission_id': sub.id,
            'student_name': student_by_id.get(sub.student_id, ''),
            'marks_awarded': sum((q.get('marks_awarded') or 0) for q in (result.get('questions') or [])) or None,
            'marks_total': sum((q.get('marks_total') or 0) for q in (result.get('questions') or [])) or None,
            'questions': result.get('questions') or [],
            'overall_feedback': result.get('overall_feedback') or '',
            'page_count': len(pages),
        })
        valid_subs[sub.id] = len(pages)

    try:
        parsed = generate_exemplar_analysis(
            provider=asn.provider,
            model=asn.model,
            session_keys=_resolve_api_keys(asn),
            subject=asn.subject or '',
            submissions_data=submissions_data,
        )
    except Exception as e:
        logger.error(f"Exemplar analysis failed for assignment {assignment_id}: {e}")
        return jsonify({'success': False, 'error': f'AI analysis failed: {e}'}), 502

    # Validate + sanitise AI output.
    areas_in = parsed.get('areas') or []
    areas_out = []
    for area in areas_in:
        if not isinstance(area, dict):
            continue
        def _clean_examples(lst):
            out = []
            seen = set()
            for ex in (lst or []):
                if not isinstance(ex, dict):
                    continue
                sid = ex.get('submission_id')
                pidx = ex.get('page_index')
                note = (ex.get('note') or '').strip()
                if not isinstance(sid, int) or sid not in valid_subs:
                    continue
                if not isinstance(pidx, int) or pidx < 0 or pidx >= valid_subs[sid]:
                    continue
                if sid in seen:
                    continue
                seen.add(sid)
                out.append({'submission_id': sid, 'page_index': pidx, 'note': note})
                if len(out) >= 2:
                    break
            return out
        needs = _clean_examples(area.get('needs_work_examples'))
        strong = _clean_examples(area.get('strong_examples'))
        if len(needs) < 2 or len(strong) < 2:
            continue
        areas_out.append({
            'question_part': (area.get('question_part') or '').strip() or 'Area',
            'label': (area.get('label') or '').strip() or 'Discussion area',
            'description': (area.get('description') or '').strip(),
            'needs_work_examples': needs,
            'strong_examples': strong,
        })

    if not areas_out:
        return jsonify({'success': False, 'error': 'AI analysis could not produce valid exemplars. Try regenerating.'}), 502

    sanitised = {'areas': areas_out}
    asn.exemplar_analysis_json = json.dumps(sanitised)
    asn.exemplar_analyzed_at = datetime.now(timezone.utc)
    db.session.commit()

    # Build student-name map for the response.
    ids = set()
    for area in areas_out:
        for ex in area['needs_work_examples'] + area['strong_examples']:
            ids.add(ex['submission_id'])
    rows = (
        db.session.query(Submission, Student)
        .join(Student, Submission.student_id == Student.id)
        .filter(Submission.id.in_(ids))
        .all()
    )
    student_names = {sub.id: st.name for (sub, st) in rows}

    return jsonify({
        'success': True,
        'analysis': sanitised,
        'student_names': student_names,
        'analyzed_at': asn.exemplar_analyzed_at.isoformat(),
    })
```

- [ ] **Step 4: Syntax check**

```bash
python3 -m py_compile app.py
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat(api): exemplar analysis endpoints (page + generate)"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 4: Exemplar Analysis page template

**Files:**
- Create: `templates/exemplars.html`

- [ ] **Step 1: Create the template**

Create `templates/exemplars.html` with:

```jinja
{% extends "base.html" %}
{% block title %}Exemplar Analysis — {{ assignment.title or assignment.subject or 'Assignment' }} — {{ app_title }}{% endblock %}

{% block head %}
<style>
    .ex-root { max-width: 1200px; margin: 0 auto; padding: 20px; }
    .ex-back { font-size: 13px; color: #667eea; text-decoration: none; }
    .ex-back:hover { text-decoration: underline; }
    .ex-header-card { background: white; border-radius: 12px; padding: 20px 24px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); margin: 12px 0 16px; }
    .ex-header-card h2 { margin: 0 0 4px; font-size: 20px; }
    .ex-header-card .meta { font-size: 13px; color: #888; }
    .ex-header-card .actions { margin-top: 12px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .ex-gen-btn { padding: 10px 18px; border: none; border-radius: 8px; background: #667eea; color: white; font-size: 14px; font-weight: 600; cursor: pointer; }
    .ex-gen-btn:hover { background: #5a6fd6; }
    .ex-gen-btn:disabled { background: #bbb; cursor: not-allowed; }
    .ex-gen-note { font-size: 12px; color: #888; }
    .ex-error { color: #b00020; font-size: 13px; margin-top: 8px; display: none; }

    .ex-areas-card { background: white; border-radius: 12px; padding: 20px 24px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); margin-bottom: 16px; }
    .ex-areas-card h3 { margin: 0 0 12px; font-size: 15px; color: #555; text-transform: uppercase; letter-spacing: 0.3px; }
    .ex-area-btns { display: flex; flex-wrap: wrap; gap: 10px; }
    .ex-area-btn {
        padding: 10px 14px; border: 1px solid #d8d8dc; border-radius: 10px; background: white;
        text-align: left; font-size: 13px; cursor: pointer; color: #2D2D2D; max-width: 380px;
        transition: border-color 0.15s, background 0.15s;
    }
    .ex-area-btn:hover { border-color: #667eea; background: #f7f8ff; }
    .ex-area-btn.active { border-color: #667eea; background: #eef1ff; }
    .ex-area-btn .qp { color: #667eea; font-weight: 700; margin-right: 6px; }
    .ex-empty { padding: 40px 20px; text-align: center; color: #888; font-size: 14px; font-style: italic; }

    .ex-view-card { background: white; border-radius: 12px; padding: 20px 24px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); min-height: 300px; }
    .ex-view-header { margin-bottom: 14px; }
    .ex-view-header h3 { margin: 0 0 4px; font-size: 17px; }
    .ex-view-header p { margin: 0; font-size: 13px; color: #555; line-height: 1.5; }
    .ex-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .ex-row-label { grid-column: 1 / -1; font-size: 12px; font-weight: 700; color: #555; text-transform: uppercase; letter-spacing: 0.4px; margin-top: 6px; padding: 4px 0 0; }
    .ex-row-label.needs { color: #c0392b; }
    .ex-row-label.strong { color: #28a745; }

    .ex-tile { border: 1px solid #eee; border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; background: #fafafa; min-height: 360px; }
    .ex-tile-header { padding: 10px 12px; background: white; border-bottom: 1px solid #eee; }
    .ex-tile-student { font-size: 13px; font-weight: 700; color: #2D2D2D; }
    .ex-tile-note { font-size: 12px; color: #666; margin-top: 2px; line-height: 1.4; }
    .ex-tile-toolbar { display: flex; gap: 6px; padding: 6px 10px; background: #f5f5f7; border-bottom: 1px solid #eee; }
    .ex-tile-toolbar button { padding: 4px 10px; font-size: 12px; border: 1px solid #d0d0d0; border-radius: 5px; background: white; cursor: pointer; }
    .ex-tile-toolbar button:hover { background: #eee; }
    .ex-tile-scroll { flex: 1; overflow: auto; padding: 10px; background: #f0f0f3; min-height: 200px; cursor: grab; }
    .ex-tile-scroll .dv-scale-wrap { transform-origin: top center; }
</style>
{% endblock %}

{% block body %}
<div class="ex-root">
    <a class="ex-back" href="/teacher/assignment/{{ assignment.id }}">&larr; Back to Assignment</a>

    <div class="ex-header-card">
        <h2>Exemplar Analysis</h2>
        <div class="meta">
            {{ assignment.title or assignment.subject or 'Assignment' }} — {{ done_count }} of {{ total_students }} students done ({{ gate_pct }}%)
            {% if analyzed_at %} · Last generated {{ analyzed_at.strftime('%d %b %Y, %H:%M') }}{% endif %}
        </div>
        <div class="actions">
            <button id="exGenBtn" class="ex-gen-btn" type="button" {% if not can_generate %}disabled title="At least 20% of the class must have done submissions before generating (currently {{ gate_pct }}%)."{% endif %}>
                {% if analysis %}Regenerate{% else %}Generate Analysis{% endif %}
            </button>
            {% if not can_generate %}
            <span class="ex-gen-note">At least 20% of the class must have done submissions (currently {{ gate_pct }}%).</span>
            {% else %}
            <span class="ex-gen-note">Generation may take 10–30 seconds.</span>
            {% endif %}
        </div>
        <div id="exError" class="ex-error"></div>
    </div>

    <div class="ex-areas-card">
        <h3>Areas for Analysis</h3>
        <div id="exAreaBtns" class="ex-area-btns">
            {% if analysis and analysis.areas %}
                {% for area in analysis.areas %}
                <button class="ex-area-btn" type="button" data-idx="{{ loop.index0 }}"><span class="qp">{{ area.question_part }}</span>{{ area.label }}</button>
                {% endfor %}
            {% else %}
                <div class="ex-empty" style="flex:1;">No analysis yet. Click {% if can_generate %}<strong>Generate Analysis</strong>{% else %}Generate Analysis (disabled){% endif %} above.</div>
            {% endif %}
        </div>
    </div>

    <div class="ex-view-card">
        <div id="exView">
            <div class="ex-empty">Select an area to analyse.</div>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/pdfjs-dist@4.8.69/build/pdf.min.mjs" type="module"></script>
<script type="module">
    import * as pdfjsLib from 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.8.69/build/pdf.min.mjs';
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.8.69/build/pdf.worker.min.mjs';
    window.pdfjsLib = pdfjsLib;
    window.dispatchEvent(new Event('pdfjs-ready'));
</script>
<script src="{{ url_for('static', filename='js/document_viewer.js') }}"></script>
<script>
var EX_ASSIGNMENT_ID = '{{ assignment.id }}';
var exAnalysis = {{ (analysis or {'areas': []}) | tojson }};
var exStudentNames = {{ student_names | tojson }};
var exViewers = [];

function exEsc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

function destroyTileViewers() {
    exViewers = [];  // module has no destroy; replacing DOM below drops references
}

function renderArea(idx) {
    var area = (exAnalysis.areas || [])[idx];
    var view = document.getElementById('exView');
    if (!area) {
        view.innerHTML = '<div class="ex-empty">Select an area to analyse.</div>';
        return;
    }
    document.querySelectorAll('.ex-area-btn').forEach(function (b) {
        b.classList.toggle('active', b.getAttribute('data-idx') === String(idx));
    });

    var html =
        '<div class="ex-view-header"><h3>' + exEsc(area.question_part) + ' — ' + exEsc(area.label) + '</h3>' +
        (area.description ? '<p>' + exEsc(area.description) + '</p>' : '') + '</div>' +
        '<div class="ex-grid">' +
            '<div class="ex-row-label needs">Needs work</div>' +
            tileHtml(area.needs_work_examples[0], 'nw0') +
            tileHtml(area.needs_work_examples[1], 'nw1') +
            '<div class="ex-row-label strong">Strong</div>' +
            tileHtml(area.strong_examples[0], 's0') +
            tileHtml(area.strong_examples[1], 's1') +
        '</div>';
    view.innerHTML = html;
    destroyTileViewers();
    mountTile('nw0', area.needs_work_examples[0]);
    mountTile('nw1', area.needs_work_examples[1]);
    mountTile('s0', area.strong_examples[0]);
    mountTile('s1', area.strong_examples[1]);
}

function tileHtml(ex, slot) {
    if (!ex) return '';
    var name = exStudentNames[ex.submission_id] || ('Submission ' + ex.submission_id);
    return (
        '<div class="ex-tile">' +
            '<div class="ex-tile-header">' +
                '<div class="ex-tile-student">' + exEsc(name) + '</div>' +
                (ex.note ? '<div class="ex-tile-note">' + exEsc(ex.note) + '</div>' : '') +
            '</div>' +
            '<div class="ex-tile-toolbar">' +
                '<button type="button" data-act="zoom-out">−</button>' +
                '<button type="button" data-act="zoom-in">+</button>' +
                '<button type="button" data-act="reset">Reset</button>' +
                '<button type="button" data-act="rotate">⟳</button>' +
            '</div>' +
            '<div class="ex-tile-scroll" id="exTile-' + slot + '"></div>' +
        '</div>'
    );
}

function mountTile(slot, ex) {
    if (!ex) return;
    var scroll = document.getElementById('exTile-' + slot);
    if (!scroll) return;
    var url = '/teacher/assignment/' + EX_ASSIGNMENT_ID + '/submission/' + ex.submission_id + '/script/page/' + ex.page_index;
    var viewer = DocumentViewer.create(scroll);
    viewer.loadFromUrl(url);
    exViewers.push(viewer);
    // Wire the per-tile toolbar
    var tile = scroll.closest('.ex-tile');
    if (tile) {
        tile.querySelectorAll('.ex-tile-toolbar button').forEach(function (btn) {
            var act = btn.getAttribute('data-act');
            btn.addEventListener('click', function () {
                if (act === 'zoom-in') viewer.zoomIn();
                else if (act === 'zoom-out') viewer.zoomOut();
                else if (act === 'reset') viewer.reset();
                else if (act === 'rotate') viewer.rotate();
            });
        });
    }
}

function wireAreaButtons() {
    document.querySelectorAll('.ex-area-btn').forEach(function (b) {
        b.addEventListener('click', function () {
            var idx = parseInt(b.getAttribute('data-idx'), 10);
            if (!isNaN(idx)) renderArea(idx);
        });
    });
}

async function generateAnalysis() {
    var btn = document.getElementById('exGenBtn');
    var errEl = document.getElementById('exError');
    errEl.style.display = 'none';
    btn.disabled = true;
    var originalLabel = btn.textContent;
    btn.textContent = 'Generating…';
    try {
        var res = await fetch('/teacher/assignment/' + EX_ASSIGNMENT_ID + '/exemplars/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        var data = await res.json();
        if (!data.success) {
            errEl.textContent = data.error || 'Generation failed.';
            errEl.style.display = 'block';
            btn.disabled = false;
            btn.textContent = originalLabel;
            return;
        }
        exAnalysis = data.analysis;
        exStudentNames = data.student_names || {};
        var btns = document.getElementById('exAreaBtns');
        var html = '';
        exAnalysis.areas.forEach(function (area, i) {
            html += '<button class="ex-area-btn" type="button" data-idx="' + i + '"><span class="qp">' + exEsc(area.question_part) + '</span>' + exEsc(area.label) + '</button>';
        });
        btns.innerHTML = html;
        wireAreaButtons();
        document.getElementById('exView').innerHTML = '<div class="ex-empty">Select an area to analyse.</div>';
        btn.textContent = 'Regenerate';
        btn.disabled = false;
    } catch (err) {
        errEl.textContent = 'Network error. Please try again.';
        errEl.style.display = 'block';
        btn.disabled = false;
        btn.textContent = originalLabel;
    }
}

(function initIfReady() {
    if (window.pdfjsLib) return;
    // Wait for pdfjs-ready before any tile mounts; area click handlers are attached regardless.
})();

document.getElementById('exGenBtn').addEventListener('click', function () {
    if (!this.disabled) generateAnalysis();
});
wireAreaButtons();
</script>
{% endblock %}
```

- [ ] **Step 2: Jinja parse check**

```bash
python3 -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('templates')); env.get_template('exemplars.html'); print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add templates/exemplars.html
git commit -m "feat(exemplars): new analysis page template with 2x2 exemplar grid"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 5: Link button on teacher_detail.html

**Files:**
- Modify: `templates/teacher_detail.html`

- [ ] **Step 1: Add the Exemplar Analysis link**

Find the row of action buttons that contains the "Class Overview & Item Analysis" link (search for `Class Overview &amp; Item Analysis`). Add a new `<a>` immediately after it:

Replace this existing line:

```jinja
                <a href="/teacher/assignment/{{ assignment.id }}/overview" target="_blank" rel="noopener" class="action-btn primary">Class Overview &amp; Item Analysis</a>
```

with:

```jinja
                <a href="/teacher/assignment/{{ assignment.id }}/overview" target="_blank" rel="noopener" class="action-btn primary">Class Overview &amp; Item Analysis</a>
                <a href="/teacher/assignment/{{ assignment.id }}/exemplars" class="action-btn primary">Exemplar Analysis</a>
```

- [ ] **Step 2: Commit**

```bash
git add templates/teacher_detail.html
git commit -m "feat(teacher): link to Exemplar Analysis page"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Final Verification

- [ ] **Step 1: Start the app**

```bash
python app.py
```

- [ ] **Step 2: End-to-end manual walkthrough**

1. Open an assignment where ≥20% of students have done submissions.
2. Click **Exemplar Analysis** on the assignment detail page — a new page opens.
3. Click **Generate Analysis**. Wait 10–30 seconds.
4. Confirm 3–8 area buttons appear, each labelled with `[question part] [label]`.
5. Click an area. Confirm the viewing area shows the area's description and a 2×2 grid with two Needs-work tiles above two Strong tiles.
6. Each tile shows a student name, a note, and a rendered page of that student's script (PDF.js). Zoom/rotate/reset work per-tile.
7. Click another area — tiles update.
8. Click **Regenerate** — analysis refreshes.
9. Open an assignment with <20% done — confirm Generate is disabled with tooltip.
10. Regression: Class Overview & Item Analysis still opens the PDF in a new tab.

---

## Self-Review (author)

**Spec coverage:**
- Schema → Task 1
- AI helper → Task 2
- Routes (page, generate) → Task 3
- Template with 2×2 grid and per-tile viewer → Task 4
- teacher_detail.html link → Task 5
- Gating at 20% (server- and client-side) → Task 3 + Task 4
- Validation of AI output (submission_id + page_index) → Task 3 generate route
- Caching on Assignment → Task 1 schema, Task 3 route

**Placeholder scan:** no TBDs, no "Similar to Task N" without code.

**Type consistency:** `generate_exemplar_analysis` signature matches the usage in Task 3. Template var names (`analysis`, `student_names`, `done_count`, `gate_pct`, `can_generate`, `analyzed_at`, `total_students`, `assignment`) are emitted by Task 3 route and consumed by Task 4 template. `DocumentViewer.create` + `loadFromUrl` match the existing module API.
