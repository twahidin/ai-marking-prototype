"""Widget metric computation for the department insights dashboard.

Loaded once per `/department/insights/widgets?band={band}` request. The
top-level `compute_widgets(band)` function builds every widget's payload
from a shared band slice — one pass over Submission.query_no_blobs() per
request keeps things snappy on schools with thousands of submissions.

Every widget is deliberately **systemic** — no teacher / student / class
appears by name in any payload. That's the design contract from
docs/superpowers/specs/2026-05-14-department-insights-widgets-design.md.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from db import (
    Assignment, Class, DepartmentGoal, FeedbackEdit, Student, Submission,
    TeacherClass,
)
from bands import (
    BAND_ALL, BAND_UNBANDED, ORDERED_BAND_KEYS, classes_in_band, resolve_band,
)

# How many days back counts as "the current term" when no explicit term
# start is configured. Rolling — not aligned to calendar terms — so the
# dashboard is useful even before the school sets term boundaries.
DEFAULT_TERM_DAYS = 91  # ~ 13 weeks

# Trend / "vs last term" delta windows.
TREND_RECENT_DAYS = 30
TREND_PRIOR_DAYS = 30

# Min-sample thresholds (centralised so the AI prompt's caveats can read
# them without re-deriving). These are intentionally conservative — fewer
# false positives is more important than maximising widget coverage.
MIN_SUBMISSIONS_FOR_DIST = 10
MIN_QUESTIONS_FOR_HOTSPOT = 10
MIN_SUBMISSIONS_FOR_AMENDED = 5
MIN_TEACHERS_FOR_ALIGNMENT = 2
MIN_EDITS_PER_TEACHER_FOR_ALIGNMENT = 3
MARKING_PIPELINE_RECENT_DAYS = 14
MARKING_PIPELINE_BASELINE_DAYS = 90

# Bucket boundaries shared between score_distribution + dept_goals(pass_rate)
PASS_THRESHOLD = 50.0


def _aware(dt):
    """Coerce naive datetimes to UTC-aware. Old Submission rows were saved
    naive — comparing aware vs naive blows up at runtime."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _submission_pct(sub, result=None):
    """Final percent score for a submission. Returns None if the result has
    no scoreable questions — caller skips such rows."""
    if result is None:
        result = sub.get_result()
    questions = result.get('questions') or []
    if not questions:
        return None
    has_marks = any(q.get('marks_awarded') is not None for q in questions)
    if has_marks:
        awarded = sum((q.get('marks_awarded') or 0) for q in questions)
        total = sum((q.get('marks_total') or 0) for q in questions)
        if total <= 0:
            return None
        return (awarded / total) * 100.0
    correct = sum(1 for q in questions if q.get('status') == 'correct')
    return (correct / len(questions)) * 100.0


# ---------------------------------------------------------------------------
# Band slice — the shared data load used by every widget on one request
# ---------------------------------------------------------------------------

class BandSlice:
    """Pre-loaded view of the data for one band tab.

    Constructed once per request; widget computations read from its
    pre-indexed dicts so no widget incurs its own SQL round-trip."""

    def __init__(self, band):
        self.band = band
        self.now = datetime.now(timezone.utc)
        self.term_start = self.now - timedelta(days=DEFAULT_TERM_DAYS)
        self.prior_term_start = self.now - timedelta(days=DEFAULT_TERM_DAYS * 2)

        all_classes = Class.query.all()
        self.classes = classes_in_band(all_classes, band)
        self.class_ids = {c.id for c in self.classes}

        # Assignments scoped to band's classes.
        if not self.class_ids:
            self.assignments = []
            self.submissions = []
            self.students = []
        else:
            self.assignments = (
                Assignment.query
                .filter(Assignment.class_id.in_(self.class_ids))
                .all()
            )
            asn_ids = {a.id for a in self.assignments}
            if asn_ids:
                q = Submission.query_no_blobs() if hasattr(Submission, 'query_no_blobs') else Submission.query
                self.submissions = q.filter(Submission.assignment_id.in_(asn_ids)).all()
            else:
                self.submissions = []
            self.students = (
                Student.query.filter(Student.class_id.in_(self.class_ids)).all()
            )

        # Index for fast joins.
        self.asn_by_id = {a.id: a for a in self.assignments}

        # Teachers participating in band (distinct via TeacherClass).
        teacher_ids = set()
        if self.class_ids:
            for row in TeacherClass.query.filter(
                TeacherClass.class_id.in_(self.class_ids)
            ).all():
                teacher_ids.add(row.teacher_id)
        self.teacher_ids = teacher_ids

        # Term-windowed views — used by most widgets so cache here.
        self.term_submissions = [
            s for s in self.submissions
            if _aware(s.submitted_at) and _aware(s.submitted_at) >= self.term_start
        ]
        self.prior_term_submissions = [
            s for s in self.submissions
            if _aware(s.submitted_at)
            and self.prior_term_start <= _aware(s.submitted_at) < self.term_start
        ]
        self.final_done_term = [
            s for s in self.term_submissions
            if s.is_final and s.status == 'done'
        ]
        self.final_done_prior = [
            s for s in self.prior_term_submissions
            if s.is_final and s.status == 'done'
        ]

        # Cache per-submission percent scores (expensive get_result calls).
        self._pct_cache = {}

    def percent(self, sub):
        if sub.id in self._pct_cache:
            return self._pct_cache[sub.id]
        pct = _submission_pct(sub)
        self._pct_cache[sub.id] = pct
        return pct


# ---------------------------------------------------------------------------
# Widget computations
# ---------------------------------------------------------------------------

def compute_band_health(slice_):
    pcts = [slice_.percent(s) for s in slice_.final_done_term]
    pcts = [p for p in pcts if p is not None]
    prior_pcts = [slice_.percent(s) for s in slice_.final_done_prior]
    prior_pcts = [p for p in prior_pcts if p is not None]

    avg_score = round(sum(pcts) / len(pcts), 1) if pcts else None
    prior_avg = round(sum(prior_pcts) / len(prior_pcts), 1) if prior_pcts else None
    avg_delta = (round(avg_score - prior_avg, 1)
                 if avg_score is not None and prior_avg is not None else None)

    # Submission rate: marked / (students_in_band × assignments_term_count)
    asn_term_ids = {
        a.id for a in slice_.assignments
        if _aware(a.created_at) and _aware(a.created_at) >= slice_.term_start
    }
    marked_count = sum(
        1 for s in slice_.final_done_term if s.assignment_id in asn_term_ids
    )
    eligible = len(slice_.students) * len(asn_term_ids)
    sub_rate = (round(marked_count / eligible * 100, 1)
                if eligible > 0 else None)

    # Prior term submission rate (for delta)
    prior_asn_ids = {
        a.id for a in slice_.assignments
        if _aware(a.created_at)
        and slice_.prior_term_start <= _aware(a.created_at) < slice_.term_start
    }
    prior_marked = sum(
        1 for s in slice_.final_done_prior if s.assignment_id in prior_asn_ids
    )
    prior_eligible = len(slice_.students) * len(prior_asn_ids)
    prior_sub_rate = (round(prior_marked / prior_eligible * 100, 1)
                      if prior_eligible > 0 else None)
    sub_rate_delta = (round(sub_rate - prior_sub_rate, 1)
                      if sub_rate is not None and prior_sub_rate is not None else None)

    # Weekly sparkline — avg %-score per week over last 8 weeks.
    weeks = []
    for w in range(8, 0, -1):
        end = slice_.now - timedelta(weeks=w - 1)
        start = end - timedelta(weeks=1)
        wk_pcts = [
            slice_.percent(s) for s in slice_.final_done_term
            if _aware(s.submitted_at) and start <= _aware(s.submitted_at) < end
        ]
        wk_pcts = [p for p in wk_pcts if p is not None]
        weeks.append(round(sum(wk_pcts) / len(wk_pcts), 1) if wk_pcts else None)

    return {
        'classes_count': len(slice_.classes),
        'teachers_count': len(slice_.teacher_ids),
        'assignments_term_count': len(asn_term_ids),
        'avg_score': avg_score,
        'avg_score_delta_vs_last_term': avg_delta,
        'submission_rate': sub_rate,
        'submission_rate_delta_vs_last_term': sub_rate_delta,
        'sparkline_weeks': weeks,
        'low_sample': len(asn_term_ids) < 3,
    }


def compute_score_distribution(slice_):
    pcts = [slice_.percent(s) for s in slice_.final_done_term]
    pcts = [p for p in pcts if p is not None]
    total = len(pcts)
    buckets = {'0-20': 0, '21-40': 0, '41-60': 0, '61-80': 0, '81-100': 0}
    for p in pcts:
        if p <= 20:
            buckets['0-20'] += 1
        elif p <= 40:
            buckets['21-40'] += 1
        elif p <= 60:
            buckets['41-60'] += 1
        elif p <= 80:
            buckets['61-80'] += 1
        else:
            buckets['81-100'] += 1
    return {
        'buckets': buckets,
        'total': total,
        'low_sample': total < MIN_SUBMISSIONS_FOR_DIST,
    }


def compute_dept_goals(slice_, viewer_role=None, viewer_subjects=None):
    """Return one row per active goal with its current progress.

    `viewer_role` / `viewer_subjects` aren't filters — every active goal
    is shown to every viewer per the spec — they're carried back so the
    UI can decide whether to render "edit" affordances per row."""
    goals = (
        DepartmentGoal.query
        .filter(DepartmentGoal.deleted_at.is_(None))
        .order_by(DepartmentGoal.created_at.desc())
        .all()
    )
    rows = []
    for g in goals:
        scoped_subs = list(slice_.final_done_term)
        out_of_band = bool(g.target_band and g.target_band != slice_.band)

        if g.target_subject:
            target_s = g.target_subject.strip().lower()
            scoped_subs = [
                s for s in scoped_subs
                if (slice_.asn_by_id.get(s.assignment_id) and
                    (slice_.asn_by_id[s.assignment_id].subject or '').strip().lower()
                    == target_s)
            ]

        progress = None
        denom = None
        numer = None
        if g.metric_type == 'pass_rate':
            pcts = [slice_.percent(s) for s in scoped_subs]
            pcts = [p for p in pcts if p is not None]
            if pcts:
                passed = sum(1 for p in pcts if p >= PASS_THRESHOLD)
                progress = passed / len(pcts) * 100.0
                numer, denom = passed, len(pcts)
        elif g.metric_type == 'avg_score':
            pcts = [slice_.percent(s) for s in scoped_subs]
            pcts = [p for p in pcts if p is not None]
            if pcts:
                progress = sum(pcts) / len(pcts)
                numer, denom = round(progress, 1), 100
        elif g.metric_type == 'submission_rate':
            assignments_in_scope = [
                a for a in slice_.assignments
                if (not g.target_subject or
                    (a.subject or '').strip().lower() == g.target_subject.strip().lower())
            ]
            asn_universe = len(assignments_in_scope)
            student_universe = len(slice_.students)
            eligible = asn_universe * student_universe
            if eligible > 0:
                progress = len(scoped_subs) / eligible * 100.0
                numer, denom = len(scoped_subs), eligible

        ratio = (progress / g.target_value) if g.target_value else 0
        if progress is None:
            status = 'no_data'
        elif ratio >= 1.0:
            status = 'done'
        elif ratio >= 0.8:
            status = 'on_track'
        elif ratio >= 0.5:
            status = 'behind'
        else:
            status = 'off_track'

        rows.append({
            'id': g.id,
            'title': g.title,
            'metric_type': g.metric_type,
            'target_value': g.target_value,
            'target_band': g.target_band,
            'target_subject': g.target_subject,
            'progress': round(progress, 1) if progress is not None else None,
            'numer': numer,
            'denom': denom,
            'status': status,
            'out_of_band': out_of_band,
            'created_by_id': g.created_by_id,
        })

    # Dept-wide goals first, then subject-specific.
    rows.sort(key=lambda r: (1 if r['target_subject'] else 0, r['title']))
    return rows


def compute_frequently_amended(slice_):
    """Per (assignment_id, criterion_id), edit_count / submission_count.

    Names neither teacher nor student. Top 8 by rate; assignments with
    fewer than `MIN_SUBMISSIONS_FOR_AMENDED` submissions are excluded."""
    submissions_by_asn = defaultdict(list)
    for s in slice_.final_done_term:
        submissions_by_asn[s.assignment_id].append(s)

    sub_ids_in_band = {s.id for s in slice_.final_done_term}
    if not sub_ids_in_band:
        return []

    edits = (
        FeedbackEdit.query
        .filter(FeedbackEdit.submission_id.in_(sub_ids_in_band))
        .filter(FeedbackEdit.active.is_(True))
        .all()
    )
    edit_counts = Counter()
    for e in edits:
        edit_counts[(e.assignment_id, e.criterion_id)] += 1

    rows = []
    for (asn_id, crit_id), n_edits in edit_counts.items():
        n_subs = len(submissions_by_asn.get(asn_id, []))
        if n_subs < MIN_SUBMISSIONS_FOR_AMENDED:
            continue
        asn = slice_.asn_by_id.get(asn_id)
        if not asn:
            continue
        rows.append({
            'assignment_title': asn.title or asn.subject or 'Untitled',
            'criterion_id': crit_id,
            'edit_count': n_edits,
            'submission_count': n_subs,
            'rate': round(n_edits / n_subs * 100, 1),
        })
    rows.sort(key=lambda r: -r['rate'])
    return rows[:8]


def compute_calibration_alignment(slice_):
    """For each (assignment, criterion) in band with ≥2 contributing
    teachers, score teacher agreement on the modal `theme_key`.

    Per-band aggregate (no per-teacher rows). Limited to criteria where
    each contributing teacher has at least
    `MIN_EDITS_PER_TEACHER_FOR_ALIGNMENT` edits."""
    sub_ids = {s.id for s in slice_.final_done_term}
    if not sub_ids:
        return []
    edits = (
        FeedbackEdit.query
        .filter(FeedbackEdit.submission_id.in_(sub_ids))
        .filter(FeedbackEdit.active.is_(True))
        .filter(FeedbackEdit.theme_key.isnot(None))
        .all()
    )
    grouped = defaultdict(lambda: defaultdict(Counter))
    for e in edits:
        grouped[(e.assignment_id, e.criterion_id)][e.edited_by][e.theme_key] += 1

    rows = []
    for (asn_id, crit_id), per_teacher in grouped.items():
        top_themes = []
        for tid, themes in per_teacher.items():
            if sum(themes.values()) < MIN_EDITS_PER_TEACHER_FOR_ALIGNMENT:
                continue
            top_themes.append(themes.most_common(1)[0][0])
        if len(top_themes) < MIN_TEACHERS_FOR_ALIGNMENT:
            continue
        modal_counter = Counter(top_themes)
        top_two_keys = [k for k, _ in modal_counter.most_common(2)]
        agreed = sum(1 for t in top_themes if t in top_two_keys)
        alignment = agreed / len(top_themes) * 100.0
        if alignment >= 75:
            bucket = 'high'
        elif alignment >= 50:
            bucket = 'mid'
        else:
            bucket = 'low'
        asn = slice_.asn_by_id.get(asn_id)
        rows.append({
            'assignment_title': (asn.title or asn.subject or 'Untitled') if asn else 'Unknown',
            'criterion_id': crit_id,
            'teachers_contributing': len(top_themes),
            'alignment_pct': round(alignment, 0),
            'bucket': bucket,
        })

    rows.sort(key=lambda r: (r['alignment_pct'], -r['teachers_contributing']))
    return rows[:10]


def compute_partial_credit_hotspots(slice_):
    """Per (assignment, question_number), partial_rate.

    A question is "partial" if its status is 'partial' OR (when marks are
    awarded) marks_awarded sits in the 10-90% band of marks_total."""
    counts = defaultdict(lambda: {'partial': 0, 'total': 0, 'title': ''})

    for sub in slice_.final_done_term:
        result = sub.get_result()
        questions = result.get('questions') or []
        if not questions:
            continue
        asn = slice_.asn_by_id.get(sub.assignment_id)
        asn_label = (asn.title or asn.subject or 'Untitled') if asn else 'Unknown'
        for i, q in enumerate(questions):
            qnum = str(q.get('question_number', i + 1))
            key = (sub.assignment_id, qnum)
            counts[key]['total'] += 1
            counts[key]['title'] = asn_label
            status = (q.get('status') or '').lower()
            if status == 'partial':
                counts[key]['partial'] += 1
                continue
            awarded = q.get('marks_awarded')
            total_m = q.get('marks_total')
            if awarded is None or not total_m:
                continue
            try:
                ratio = float(awarded) / float(total_m)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if 0.1 <= ratio <= 0.9:
                counts[key]['partial'] += 1

    rows = []
    for (asn_id, qnum), c in counts.items():
        if c['total'] < MIN_QUESTIONS_FOR_HOTSPOT:
            continue
        rows.append({
            'assignment_title': c['title'],
            'question_number': qnum,
            'partial_rate': round(c['partial'] / c['total'] * 100, 1),
            'total': c['total'],
        })
    rows.sort(key=lambda r: -r['partial_rate'])
    return rows[:8]


def compute_persistent_gap(slice_):
    """% of band students with ≥4 final submissions who scored <40% on
    ≥3 of their last 4 assessments. Single big-number widget."""
    n_band = len(slice_.students)
    if not n_band:
        return {'pct': None, 'n_qualified': 0, 'n_band_total': 0, 'low_sample': True}

    by_student = defaultdict(list)
    for s in slice_.final_done_term:
        by_student[s.student_id].append(s)

    qualified = 0
    flagged = 0
    for st in slice_.students:
        subs = by_student.get(st.id, [])
        if len(subs) < 4:
            continue
        subs_sorted = sorted(
            subs, key=lambda s: _aware(s.submitted_at) or slice_.term_start,
            reverse=True
        )[:4]
        pcts = [slice_.percent(s) for s in subs_sorted]
        pcts = [p for p in pcts if p is not None]
        if len(pcts) < 4:
            continue
        qualified += 1
        if sum(1 for p in pcts if p < 40) >= 3:
            flagged += 1

    low_sample = qualified < (n_band * 0.5)
    return {
        'pct': round(flagged / qualified * 100, 1) if qualified else None,
        'n_qualified': qualified,
        'n_band_total': n_band,
        'low_sample': low_sample,
    }


def compute_marking_pipeline(slice_):
    """Submitted vs marked vs pending in last 14 days, against the prior
    90-day baseline ratio. Band-aggregate, never per teacher."""
    recent_cutoff = slice_.now - timedelta(days=MARKING_PIPELINE_RECENT_DAYS)
    baseline_cutoff = slice_.now - timedelta(days=MARKING_PIPELINE_BASELINE_DAYS)

    recent = [s for s in slice_.submissions
              if _aware(s.submitted_at) and _aware(s.submitted_at) >= recent_cutoff
              and s.is_final]
    baseline = [s for s in slice_.submissions
                if _aware(s.submitted_at) and _aware(s.submitted_at) >= baseline_cutoff
                and s.is_final]

    submitted = len(recent)
    marked = sum(1 for s in recent if s.status == 'done')
    pending = submitted - marked

    base_submitted = len(baseline)
    base_marked = sum(1 for s in baseline if s.status == 'done')
    pending_share_now = (pending / submitted) if submitted else None
    pending_share_baseline = (
        (base_submitted - base_marked) / base_submitted if base_submitted else None
    )

    return {
        'submitted': submitted,
        'marked': marked,
        'pending': pending,
        'pending_share_now': round(pending_share_now * 100, 1) if pending_share_now is not None else None,
        'pending_share_baseline': round(pending_share_baseline * 100, 1) if pending_share_baseline is not None else None,
        'low_sample': submitted < 10,
    }


def compute_assessment_rhythm(slice_):
    """Assignments per fortnight bin from term start to now, plus the
    median fortnightly rate across all bands for context."""
    term_asns = [
        a for a in slice_.assignments
        if _aware(a.created_at) and _aware(a.created_at) >= slice_.term_start
    ]
    bins = []
    cursor = slice_.term_start
    while cursor < slice_.now:
        end = min(cursor + timedelta(days=14), slice_.now)
        n = sum(1 for a in term_asns
                if _aware(a.created_at)
                and cursor <= _aware(a.created_at) < end)
        bins.append({
            'start': cursor.date().isoformat(),
            'end': end.date().isoformat(),
            'count': n,
        })
        cursor = end

    band_rate = sum(b['count'] for b in bins) / len(bins) if bins else 0

    other_rates = []
    for other_band in ORDERED_BAND_KEYS:
        if other_band == slice_.band:
            other_rates.append(band_rate)
            continue
        other_classes = [c for c in Class.query.all() if resolve_band(c.level) == other_band]
        other_class_ids = {c.id for c in other_classes}
        if not other_class_ids:
            continue
        other_asns = (
            Assignment.query
            .filter(Assignment.class_id.in_(other_class_ids))
            .filter(Assignment.created_at >= slice_.term_start)
            .all()
        )
        n_bins = max(1, len(bins))
        other_rates.append(len(other_asns) / n_bins)
    median_across = round(statistics.median(other_rates), 2) if other_rates else 0

    return {
        'bins': bins,
        'band_rate': round(band_rate, 2),
        'median_across_bands': median_across,
    }


def compute_wins_to_share(slice_):
    """Heuristic wins. Each item is independent — present only when its
    underlying datum clears the threshold. Anything that would name a
    teacher / class is silently excluded."""
    wins = []

    win28 = slice_.now - timedelta(days=28)
    win56 = slice_.now - timedelta(days=56)
    recent_done = [s for s in slice_.final_done_term
                   if _aware(s.submitted_at) and _aware(s.submitted_at) >= win28]
    prior_done = [s for s in slice_.final_done_term
                  if _aware(s.submitted_at)
                  and win56 <= _aware(s.submitted_at) < win28]
    recent_asn_ids = {
        a.id for a in slice_.assignments
        if _aware(a.created_at) and _aware(a.created_at) >= win28
    }
    prior_asn_ids = {
        a.id for a in slice_.assignments
        if _aware(a.created_at)
        and win56 <= _aware(a.created_at) < win28
    }
    n_students = len(slice_.students)
    if n_students and recent_asn_ids and prior_asn_ids:
        recent_rate = len(recent_done) / (n_students * len(recent_asn_ids)) * 100
        prior_rate = len(prior_done) / (n_students * len(prior_asn_ids)) * 100
        if recent_rate - prior_rate >= 5:
            wins.append({
                'glyph': '⬆',
                'text': f'Submission rate up {round(recent_rate - prior_rate, 1)} pp in the last 4 weeks.',
            })

    rp = [slice_.percent(s) for s in recent_done]
    rp = [p for p in rp if p is not None]
    pp = [slice_.percent(s) for s in prior_done]
    pp = [p for p in pp if p is not None]
    if rp and pp:
        delta = sum(rp) / len(rp) - sum(pp) / len(pp)
        if delta >= 3:
            wins.append({
                'glyph': '⬆',
                'text': f'Average band score up {round(delta, 1)} pp vs the prior 4 weeks.',
            })

    pipeline = compute_marking_pipeline(slice_)
    if (pipeline['pending_share_now'] is not None
            and pipeline['pending_share_baseline'] is not None
            and pipeline['pending_share_now'] + 5 < pipeline['pending_share_baseline']):
        wins.append({
            'glyph': '⬆',
            'text': f"Marking pipeline is fresher than the 90-day baseline "
                    f"({pipeline['pending_share_now']}% pending vs "
                    f"{pipeline['pending_share_baseline']}%).",
        })

    return wins[:5]


# ---------------------------------------------------------------------------
# AI analysis assembly
# ---------------------------------------------------------------------------

def build_low_sample_list(payload):
    """Inspect the full widgets payload and return a list of widget keys
    whose `low_sample` flag fired. The AI prompt's caveats banner reads
    this so the HOD sees what was excluded."""
    flagged = []
    for key in ('band_health', 'score_distribution', 'persistent_gap',
                'marking_pipeline'):
        if isinstance(payload.get(key), dict) and payload[key].get('low_sample'):
            flagged.append(key)
    return flagged


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------

def compute_widgets(band, viewer_role=None, viewer_subjects=None):
    """Build the full widget payload for one band tab."""
    if band not in (BAND_ALL, BAND_UNBANDED) and band not in ORDERED_BAND_KEYS:
        band = ORDERED_BAND_KEYS[0]

    slice_ = BandSlice(band)
    payload = {
        'band': band,
        'as_of': slice_.now.isoformat(),
        'term_window': {
            'start': slice_.term_start.date().isoformat(),
            'days': DEFAULT_TERM_DAYS,
        },
        'band_health': compute_band_health(slice_),
        'dept_goals': compute_dept_goals(slice_, viewer_role, viewer_subjects),
        'score_distribution': compute_score_distribution(slice_),
        'frequently_amended': compute_frequently_amended(slice_),
        'calibration_alignment': compute_calibration_alignment(slice_),
        'partial_credit_hotspots': compute_partial_credit_hotspots(slice_),
        'persistent_gap': compute_persistent_gap(slice_),
        'marking_pipeline': compute_marking_pipeline(slice_),
        'assessment_rhythm': compute_assessment_rhythm(slice_),
        'wins_to_share': compute_wins_to_share(slice_),
    }
    payload['low_sample_widgets'] = build_low_sample_list(payload)
    return payload
