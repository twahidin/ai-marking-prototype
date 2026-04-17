# Enhanced Insights Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add AI-powered analysis (summary + action items) and cross-assignment item analysis to the Department Insights page.

**Architecture:** Three new API endpoints in app.py. AI analysis uses existing `get_ai_client()` from ai_marking.py for text-only calls (no vision). Saved analyses stored in DepartmentConfig. Item analysis computes per-question % correct across selected assignments. All UI added to the existing department_insights.html template.

**Tech Stack:** Flask/Jinja2, vanilla JavaScript, existing AI provider abstraction (Anthropic/OpenAI/Qwen SDKs).

---

### Task 1: Add item analysis backend endpoint

**Files:**
- Modify: `app.py` (add endpoint after `/department/insights/data` around line 1270)

**Step 1: Add the item analysis endpoint**

Insert after the `department_insights_data()` function (around line 1270, before `department_export_csv`):

```python
@app.route('/department/insights/item-analysis')
def department_item_analysis():
    """Compare per-question performance across multiple assignments."""
    err = _require_hod()
    if err:
        return err

    ids = request.args.get('assignment_ids', '')
    assignment_ids = [x.strip() for x in ids.split(',') if x.strip()]
    if len(assignment_ids) < 2:
        return jsonify({'success': False, 'error': 'Select at least 2 assignments'}), 400

    assignments = Assignment.query.filter(Assignment.id.in_(assignment_ids)).all()
    if len(assignments) < 2:
        return jsonify({'success': False, 'error': 'Assignments not found'}), 404

    # Pre-load class names
    cls_ids = list(set(a.class_id for a in assignments if a.class_id))
    classes = {c.id: c for c in Class.query.filter(Class.id.in_(cls_ids)).all()} if cls_ids else {}

    result = []
    all_qnums = set()

    for asn in assignments:
        subs = Submission.query.filter_by(assignment_id=asn.id, status='done').all()
        q_stats = {}  # qnum -> {correct, total}
        for sub in subs:
            questions = sub.get_result().get('questions', [])
            for i, q in enumerate(questions):
                qnum = str(q.get('question_number', i + 1))
                q_stats.setdefault(qnum, {'correct': 0, 'total': 0})
                q_stats[qnum]['total'] += 1
                has_marks = q.get('marks_awarded') is not None
                if has_marks:
                    if q.get('marks_awarded', 0) == q.get('marks_total', 1):
                        q_stats[qnum]['correct'] += 1
                elif q.get('status') == 'correct':
                    q_stats[qnum]['correct'] += 1
                all_qnums.add(qnum)

        cls = classes.get(asn.class_id)
        questions_pct = {}
        for qnum, stats in q_stats.items():
            questions_pct[qnum] = round(stats['correct'] / stats['total'] * 100, 1) if stats['total'] else 0

        result.append({
            'id': asn.id,
            'title': asn.title or asn.subject or 'Untitled',
            'class_name': cls.name if cls else 'Unknown',
            'questions': questions_pct,
        })

    # Sort question numbers naturally
    def sort_key(q):
        try:
            return (0, int(q))
        except ValueError:
            return (1, q)
    sorted_qnums = sorted(all_qnums, key=sort_key)

    return jsonify({
        'success': True,
        'assignments': result,
        'question_numbers': sorted_qnums,
    })
```

**Step 2: Commit**

```bash
git add app.py
git commit -m "feat: add item analysis endpoint for cross-assignment comparison"
```

---

### Task 2: Add AI analysis backend endpoints

**Files:**
- Modify: `app.py` (add two endpoints after the item analysis endpoint)

**Step 1: Add the GET endpoint to retrieve saved analysis**

```python
@app.route('/department/insights/analysis')
def department_get_analysis():
    """Retrieve saved AI analysis for given filters."""
    err = _require_hod()
    if err:
        return err

    asn_id = request.args.get('assignment_id', 'all')
    cls_id = request.args.get('class_id', 'all')
    key = f'insight_analysis:{asn_id}:{cls_id}'

    cfg = DepartmentConfig.query.filter_by(key=key).first()
    if cfg and cfg.value:
        try:
            data = json.loads(cfg.value)
            return jsonify({'success': True, 'exists': True, **data})
        except Exception:
            pass
    return jsonify({'success': True, 'exists': False})
```

**Step 2: Add the POST endpoint to generate AI analysis**

```python
@app.route('/department/insights/analyze', methods=['POST'])
def department_analyze():
    """Generate AI analysis of insights data."""
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    provider = data.get('provider')
    model = data.get('model')
    asn_filter = data.get('assignment_id', '')
    cls_filter = data.get('class_id', '')
    item_analysis_data = data.get('item_analysis')  # optional cross-assignment data

    if not provider:
        return jsonify({'success': False, 'error': 'No provider selected'}), 400

    # Resolve API keys: dept keys → env vars
    dept_keys = _get_dept_keys()
    from ai_marking import get_ai_client, get_available_providers
    session_keys = dept_keys if dept_keys else None
    client, model_name, prov_type = get_ai_client(provider, model, session_keys)
    if not client:
        return jsonify({'success': False, 'error': f'No API key for {provider}'}), 400

    # Gather insights data (reuse logic from insights_data endpoint)
    query = Submission.query.filter_by(status='done')
    if asn_filter:
        query = query.filter_by(assignment_id=asn_filter)

    submissions = query.all()
    asn_ids = list(set(s.assignment_id for s in submissions))
    all_asns = {a.id: a for a in Assignment.query.filter(Assignment.id.in_(asn_ids)).all()} if asn_ids else {}
    cls_ids_set = list(set(a.class_id for a in all_asns.values() if a.class_id))
    all_classes = {c.id: c for c in Class.query.filter(Class.id.in_(cls_ids_set)).all()} if cls_ids_set else {}

    class_scores = {}
    question_stats = {}
    student_scores = []

    for sub in submissions:
        asn = all_asns.get(sub.assignment_id)
        if not asn or not asn.class_id:
            continue
        if cls_filter and asn.class_id != cls_filter:
            continue

        result = sub.get_result()
        questions = result.get('questions', [])
        if not questions:
            continue

        cls = all_classes.get(asn.class_id)
        cls_name = cls.name if cls else 'Unknown'
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        if has_marks:
            total_a = sum(q.get('marks_awarded', 0) for q in questions)
            total_p = sum(q.get('marks_total', 0) for q in questions)
            pct = (total_a / total_p * 100) if total_p > 0 else 0
        else:
            correct = sum(1 for q in questions if q.get('status') == 'correct')
            pct = (correct / len(questions) * 100) if questions else 0

        class_scores.setdefault(cls_name, []).append(pct)
        student_scores.append({'class': cls_name, 'score': round(pct, 1)})

        for i, q in enumerate(questions):
            qnum = str(q.get('question_number', i + 1))
            question_stats.setdefault(qnum, {'correct': 0, 'total': 0})
            question_stats[qnum]['total'] += 1
            if q.get('status') == 'correct' or (has_marks and q.get('marks_awarded', 0) == q.get('marks_total', 1)):
                question_stats[qnum]['correct'] += 1

    if not student_scores:
        return jsonify({'success': False, 'error': 'No data to analyze'}), 400

    # Build prompt data
    class_avgs = {name: round(sum(scores) / len(scores), 1) for name, scores in class_scores.items()}
    q_difficulty = {qnum: round(stats['correct'] / stats['total'] * 100, 1) if stats['total'] else 0
                    for qnum, stats in sorted(question_stats.items(), key=lambda x: x[0])}

    all_scores_flat = [s['score'] for s in student_scores]
    overall_avg = round(sum(all_scores_flat) / len(all_scores_flat), 1)
    pass_rate = round(sum(1 for s in all_scores_flat if s >= 50) / len(all_scores_flat) * 100, 1)

    # Bottom performers
    sorted_students = sorted(student_scores, key=lambda x: x['score'])
    bottom_5 = sorted_students[:5]

    # Hardest questions
    hardest = sorted(q_difficulty.items(), key=lambda x: x[1])[:5]

    prompt_data = f"""Department Performance Data:
- Total students marked: {len(all_scores_flat)}
- Overall average: {overall_avg}%
- Pass rate (>=50%): {pass_rate}%

Class averages:
{chr(10).join(f'  - {name}: {avg}%' for name, avg in class_avgs.items())}

Question difficulty (% fully correct):
{chr(10).join(f'  - Q{qnum}: {pct}%' for qnum, pct in q_difficulty.items())}

Hardest questions:
{chr(10).join(f'  - Q{qnum}: {pct}% correct' for qnum, pct in hardest)}

Lowest-scoring students:
{chr(10).join(f'  - {s["class"]}: {s["score"]}%' for s in bottom_5)}"""

    if item_analysis_data:
        prompt_data += f"""

Cross-assignment comparison (same questions, different classes):
{item_analysis_data}"""

    system_prompt = """You are an education analytics assistant. Analyze the department performance data and provide:

1. **Summary**: A 2-3 sentence overview of overall performance, highlighting key patterns and notable differences between classes.

2. **Action Items**: 3-5 specific, actionable recommendations. Each should identify WHO needs attention (which class, which students), WHAT the issue is (which topics/questions), and HOW to address it.

Respond in JSON format:
{
  "summary": "...",
  "action_items": ["...", "...", "..."]
}"""

    try:
        if prov_type == 'anthropic':
            response = client.messages.create(
                model=model_name,
                max_tokens=1024,
                system=system_prompt,
                messages=[{'role': 'user', 'content': prompt_data}],
            )
            text = response.content[0].text
        else:
            response = client.chat.completions.create(
                model=model_name,
                max_tokens=1024,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': prompt_data},
                ],
            )
            text = response.choices[0].message.content

        # Parse JSON from response
        import re as _re
        json_match = _re.search(r'\{[\s\S]*\}', text)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = {'summary': text, 'action_items': []}

        summary = parsed.get('summary', '')
        action_items = parsed.get('action_items', [])

    except Exception as e:
        logger.error(f'AI analysis failed: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

    # Save to DepartmentConfig
    asn_key = asn_filter or 'all'
    cls_key = cls_filter or 'all'
    config_key = f'insight_analysis:{asn_key}:{cls_key}'
    saved = {
        'summary': summary,
        'action_items': action_items,
        'provider': provider,
        'model': model_name,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

    cfg = DepartmentConfig.query.filter_by(key=config_key).first()
    if cfg:
        cfg.value = json.dumps(saved)
    else:
        cfg = DepartmentConfig(key=config_key, value=json.dumps(saved))
        db.session.add(cfg)
    db.session.commit()

    return jsonify({'success': True, **saved})
```

**Step 3: Add missing imports at top of app.py if not present**

Ensure `from datetime import datetime, timezone` and `import json` are imported (they likely already are — verify).

**Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add AI analysis endpoints for insights"
```

---

### Task 3: Add AI analysis UI to department_insights.html

**Files:**
- Modify: `templates/department_insights.html`

**Step 1: Add CSS for the new sections**

Add to the `<style>` block, before `</style>`:

```css
.ai-card { margin-bottom: 24px; }
.ai-controls { display: flex; gap: 12px; align-items: end; flex-wrap: wrap; margin-bottom: 16px; }
.ai-controls .filter-group { min-width: 140px; }
.ai-result { display: none; }
.ai-result.visible { display: block; }
.ai-summary {
    background: #f8f9ff; border-left: 4px solid #667eea; padding: 16px 20px;
    border-radius: 0 12px 12px 0; margin-bottom: 16px; font-size: 14px;
    line-height: 1.7; color: #333;
}
.ai-actions { list-style: none; padding: 0; }
.ai-actions li {
    padding: 12px 16px; border: 1px solid #e8e8e8; border-radius: 10px;
    margin-bottom: 8px; font-size: 13px; line-height: 1.6; color: #333;
    display: flex; align-items: flex-start; gap: 10px;
}
.ai-actions li::before { content: '\27A1'; flex-shrink: 0; margin-top: 2px; }
.ai-meta { font-size: 11px; color: #aaa; margin-top: 8px; }
.ai-loading { display: none; padding: 20px; text-align: center; color: #888; font-size: 14px; }
.ai-loading.visible { display: block; }

.item-card { margin-bottom: 24px; }
.asn-checklist { max-height: 300px; overflow-y: auto; border: 1px solid #eee; border-radius: 8px; padding: 12px; }
.asn-check-group { margin-bottom: 12px; }
.asn-check-group-label { font-size: 12px; font-weight: 600; color: #888; margin-bottom: 6px; }
.asn-check-item { display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 13px; }
.asn-check-item input { margin: 0; }
.item-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 16px; }
.item-table th { padding: 10px 12px; background: #f8f8f8; font-size: 12px; color: #888; text-align: center; border-bottom: 2px solid #eee; }
.item-table th:first-child { text-align: left; }
.item-table td { padding: 8px 12px; text-align: center; border-bottom: 1px solid #f0f0f0; }
.item-table td:first-child { text-align: left; font-weight: 600; }
.pct-good { background: #e8f5e9; color: #28a745; font-weight: 600; border-radius: 6px; }
.pct-mid { background: #fff3cd; color: #856404; font-weight: 600; border-radius: 6px; }
.pct-low { background: #fde8e8; color: #dc3545; font-weight: 600; border-radius: 6px; }
```

**Step 2: Add AI Analysis card HTML**

Insert after the export-bar div (before `{% endif %}`):

```html
<!-- AI Analysis -->
<div class="chart-card ai-card animate__animated animate__fadeIn" style="animation-delay:0.4s;animation-duration:0.5s;">
    <h3>AI Analysis</h3>
    <div class="ai-controls">
        <div class="filter-group">
            <label>Provider</label>
            <select id="aiProvider" onchange="updateAiModels()">
            </select>
        </div>
        <div class="filter-group">
            <label>Model</label>
            <select id="aiModel"></select>
        </div>
        <button class="filter-btn" id="analyzeBtn" onclick="generateAnalysis()">Generate Analysis</button>
    </div>
    <div class="ai-loading" id="aiLoading">Analyzing data... this may take a few seconds.</div>
    <div class="ai-result" id="aiResult">
        <div class="ai-summary" id="aiSummary"></div>
        <ul class="ai-actions" id="aiActions"></ul>
        <div class="ai-meta" id="aiMeta"></div>
    </div>
</div>

<!-- Item Analysis -->
<div class="chart-card item-card animate__animated animate__fadeIn" style="animation-delay:0.45s;animation-duration:0.5s;">
    <h3>Item Analysis &mdash; Compare Assignments</h3>
    <p style="font-size:13px;color:#888;margin-bottom:12px;">Select 2 or more assignments to compare per-question performance across classes.</p>
    <div class="asn-checklist" id="asnChecklist">
        {% set asn_by_class = {} %}
        {% for asn in assignments %}
            {% set cls_name = asn.dept_class.name if asn.dept_class else 'Unknown' %}
            {% if cls_name not in asn_by_class %}
                {% set _ = asn_by_class.update({cls_name: []}) %}
            {% endif %}
            {% set _ = asn_by_class[cls_name].append(asn) %}
        {% endfor %}
        {% for cls_name, asns in asn_by_class.items() %}
        <div class="asn-check-group">
            <div class="asn-check-group-label">{{ cls_name }}</div>
            {% for asn in asns %}
            <label class="asn-check-item">
                <input type="checkbox" value="{{ asn.id }}" onchange="updateCompareBtn()">
                {{ asn.title or asn.subject or 'Untitled' }}
            </label>
            {% endfor %}
        </div>
        {% endfor %}
    </div>
    <div style="margin-top:12px;">
        <button class="filter-btn" id="compareBtn" onclick="runItemAnalysis()" disabled>Compare</button>
    </div>
    <div id="itemResult"></div>
</div>
```

**Step 3: Update the template to pass providers data**

The template needs to know which providers are available for the AI selector. We'll pass this from the route. But first, add the JS that uses it. In the `<script>` block, add before the closing `</script>`:

```javascript
/* AI Analysis */
var AI_PROVIDERS = {{ ai_providers | tojson }};

function updateAiProviderSelect() {
    var sel = document.getElementById('aiProvider');
    sel.innerHTML = '';
    var providerOrder = ['anthropic', 'openai', 'qwen'];
    // Budget models first
    var budgetModels = {
        'anthropic': 'claude-haiku-4-5-20251001',
        'openai': 'gpt-5.4-mini',
        'qwen': 'qwen3.5-plus-2026-02-15'
    };
    var first = true;
    providerOrder.forEach(function(key) {
        if (!AI_PROVIDERS[key]) return;
        var o = document.createElement('option');
        o.value = key;
        o.textContent = AI_PROVIDERS[key].label;
        sel.appendChild(o);
        first = false;
    });
    updateAiModels();
}

function updateAiModels() {
    var prov = document.getElementById('aiProvider').value;
    var sel = document.getElementById('aiModel');
    sel.innerHTML = '';
    if (!prov || !AI_PROVIDERS[prov]) return;
    var models = AI_PROVIDERS[prov].models;
    var budgetModels = {
        'anthropic': 'claude-haiku-4-5-20251001',
        'openai': 'gpt-5.4-mini',
        'qwen': 'qwen3.5-plus-2026-02-15'
    };
    var defaultModel = budgetModels[prov] || AI_PROVIDERS[prov].default;
    for (var id in models) {
        var o = document.createElement('option');
        o.value = id;
        o.textContent = models[id];
        if (id === defaultModel) o.selected = true;
        sel.appendChild(o);
    }
}

function generateAnalysis() {
    var provider = document.getElementById('aiProvider').value;
    var model = document.getElementById('aiModel').value;
    var asnId = document.getElementById('filterAssignment').value;
    var clsId = document.getElementById('filterClass').value;

    if (!provider) return alert('Select a provider.');

    // Collect item analysis data if visible
    var itemData = null;
    var itemTable = document.getElementById('itemResult');
    if (itemTable && itemTable.dataset.summary) {
        itemData = itemTable.dataset.summary;
    }

    document.getElementById('aiLoading').classList.add('visible');
    document.getElementById('aiResult').classList.remove('visible');
    document.getElementById('analyzeBtn').disabled = true;

    fetch('/department/insights/analyze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            provider: provider,
            model: model,
            assignment_id: asnId,
            class_id: clsId,
            item_analysis: itemData,
        })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        document.getElementById('aiLoading').classList.remove('visible');
        document.getElementById('analyzeBtn').disabled = false;
        if (!data.success) {
            alert(data.error || 'Analysis failed.');
            return;
        }
        renderAnalysis(data);
    })
    .catch(function(err) {
        document.getElementById('aiLoading').classList.remove('visible');
        document.getElementById('analyzeBtn').disabled = false;
        alert('Connection error.');
    });
}

function renderAnalysis(data) {
    document.getElementById('aiSummary').textContent = data.summary || '';
    var ul = document.getElementById('aiActions');
    ul.innerHTML = '';
    (data.action_items || []).forEach(function(item) {
        var li = document.createElement('li');
        li.textContent = item;
        ul.appendChild(li);
    });
    var meta = '';
    if (data.provider) meta += data.provider;
    if (data.model) meta += ' / ' + data.model;
    if (data.generated_at) meta += ' — ' + new Date(data.generated_at).toLocaleString();
    document.getElementById('aiMeta').textContent = meta;
    document.getElementById('aiResult').classList.add('visible');
    document.getElementById('analyzeBtn').textContent = 'Regenerate';
}

function loadSavedAnalysis() {
    var asnId = document.getElementById('filterAssignment').value || '';
    var clsId = document.getElementById('filterClass').value || '';
    var params = new URLSearchParams();
    if (asnId) params.set('assignment_id', asnId);
    if (clsId) params.set('class_id', clsId);

    fetch('/department/insights/analysis?' + params.toString())
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success && data.exists) {
            renderAnalysis(data);
        } else {
            document.getElementById('aiResult').classList.remove('visible');
            document.getElementById('analyzeBtn').textContent = 'Generate Analysis';
        }
    });
}

/* Item Analysis */
function updateCompareBtn() {
    var checked = document.querySelectorAll('#asnChecklist input:checked');
    document.getElementById('compareBtn').disabled = checked.length < 2;
}

function runItemAnalysis() {
    var checked = document.querySelectorAll('#asnChecklist input:checked');
    var ids = [];
    checked.forEach(function(cb) { ids.push(cb.value); });
    if (ids.length < 2) return;

    document.getElementById('compareBtn').disabled = true;
    document.getElementById('compareBtn').textContent = 'Loading...';

    fetch('/department/insights/item-analysis?assignment_ids=' + ids.join(','))
    .then(function(r) { return r.json(); })
    .then(function(data) {
        document.getElementById('compareBtn').disabled = false;
        document.getElementById('compareBtn').textContent = 'Compare';
        if (!data.success) {
            alert(data.error || 'Failed.');
            return;
        }
        renderItemAnalysis(data);
    })
    .catch(function() {
        document.getElementById('compareBtn').disabled = false;
        document.getElementById('compareBtn').textContent = 'Compare';
        alert('Connection error.');
    });
}

function renderItemAnalysis(data) {
    var el = document.getElementById('itemResult');
    if (!data.assignments.length || !data.question_numbers.length) {
        el.innerHTML = '<div class="no-data" style="margin-top:16px;">No submission data for selected assignments.</div>';
        return;
    }

    var html = '<table class="item-table"><thead><tr><th>Question</th>';
    data.assignments.forEach(function(a) {
        html += '<th>' + esc(a.class_name) + '<br><span style="font-weight:400;font-size:11px;">' + esc(a.title) + '</span></th>';
    });
    html += '</tr></thead><tbody>';

    // Build summary for AI prompt
    var summaryLines = [];

    data.question_numbers.forEach(function(qnum) {
        html += '<tr><td>Q' + esc(qnum) + '</td>';
        var line = 'Q' + qnum + ': ';
        data.assignments.forEach(function(a) {
            var pct = a.questions[qnum];
            var cls = 'pct-low';
            if (pct == null) {
                html += '<td>—</td>';
                line += a.class_name + ' N/A, ';
            } else {
                if (pct >= 70) cls = 'pct-good';
                else if (pct >= 50) cls = 'pct-mid';
                html += '<td class="' + cls + '">' + pct + '%</td>';
                line += a.class_name + ' ' + pct + '%, ';
            }
        });
        summaryLines.push(line);
        html += '</tr>';
    });

    html += '</tbody></table>';
    el.innerHTML = html;
    el.dataset.summary = summaryLines.join('\n');
}

// Init
updateAiProviderSelect();
loadSavedAnalysis();
```

**Step 4: Also update `loadInsights()` to reload saved analysis when filters change**

At the end of the `loadInsights()` function's `.then` callback, add:

```javascript
        loadSavedAnalysis();
```

**Step 5: Commit**

```bash
git add templates/department_insights.html
git commit -m "feat: add AI analysis and item analysis UI to insights page"
```

---

### Task 4: Update insights route to pass AI providers

**Files:**
- Modify: `app.py` (department_insights route, around line 1165)

**Step 1: Update the route to pass available providers**

Change the `department_insights()` function to also pass available AI providers:

```python
@app.route('/department/insights')
def department_insights():
    err = _require_hod()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    assignments = Assignment.query.filter(Assignment.class_id.isnot(None))\
        .order_by(Assignment.created_at.desc()).all()

    # Get available AI providers for analysis
    from ai_marking import get_available_providers, PROVIDERS
    dept_keys = _get_dept_keys()
    ai_providers = get_available_providers(dept_keys) if dept_keys else get_available_providers()
    # If no dept keys and no env keys, show all providers (they'll need keys)
    if not ai_providers:
        ai_providers = PROVIDERS

    return render_template('department_insights.html',
                           teacher=teacher,
                           classes=classes,
                           assignments=assignments,
                           ai_providers=ai_providers,
                           demo_mode=is_demo_mode(),
                           dept_mode=is_dept_mode())
```

**Step 2: Commit**

```bash
git add app.py
git commit -m "feat: pass AI providers to insights template"
```

---

### Task 5: Fix Jinja grouping for item analysis checklist

**Files:**
- Modify: `templates/department_insights.html` (the assignment checklist)

The Jinja2 `{% set _ = dict.update() %}` pattern doesn't work reliably. Replace the checklist with a simpler approach using `groupby`:

**Step 1: Replace the checklist HTML**

Replace the `asn-checklist` div content with:

```html
<div class="asn-checklist" id="asnChecklist">
    {% for asn in assignments %}
    <label class="asn-check-item">
        <input type="checkbox" value="{{ asn.id }}" onchange="updateCompareBtn()">
        <span style="color:#667eea;font-weight:600;">{{ asn.dept_class.name if asn.dept_class else 'Unknown' }}</span>
        &mdash; {{ asn.title or asn.subject or 'Untitled' }}
    </label>
    {% endfor %}
</div>
```

This is simpler and avoids Jinja dict mutation issues. Each line shows "ClassName — AssignmentTitle".

**Step 2: Commit**

```bash
git add templates/department_insights.html
git commit -m "fix: simplify item analysis checklist to avoid Jinja grouping issues"
```

---

### Task 6: Test and verify

**Step 1: Verify app starts**

```bash
python3 -c "from app import app; print('OK')"
```

**Step 2: Verify endpoints exist**

```bash
python3 -c "
from app import app
with app.test_client() as c:
    print('GET /department/insights:', c.get('/department/insights').status_code)
    print('GET /department/insights/analysis:', c.get('/department/insights/analysis').status_code)
    print('GET /department/insights/item-analysis:', c.get('/department/insights/item-analysis').status_code)
"
```

Expected: 302 (redirect for unauthenticated), 401, 400 respectively.

**Step 3: Final commit**

```bash
git add -A
git commit -m "refactor: enhanced insights — AI analysis and item analysis"
```
