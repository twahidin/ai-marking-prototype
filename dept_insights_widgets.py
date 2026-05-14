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

import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from db import (
    Assignment, Class, DepartmentConfig, DepartmentGoal, FeedbackEdit, Student,
    Submission, TeacherClass,
)
from bands import (
    BAND_ALL, BAND_UNBANDED, ORDERED_BAND_KEYS, classes_in_band, resolve_band,
)
from moe_terms import current_term as moe_current_term, most_recent_term as moe_most_recent_term

# How many days back counts as "the current term" when no explicit term
# start is configured. Rolling — not aligned to calendar terms — so the
# dashboard is useful even before the school sets term boundaries.
# Sized to match a Singapore MOE secondary school term (10 weeks).
DEFAULT_TERM_DAYS = 70  # 10 weeks

# Trend / "vs last term" delta windows.
TREND_RECENT_DAYS = 30
TREND_PRIOR_DAYS = 30

# Min-sample thresholds (centralised so the AI prompt's caveats can read
# them without re-deriving). These are intentionally conservative — fewer
# false positives is more important than maximising widget coverage.
MIN_SUBMISSIONS_FOR_DIST = 10
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


def _load_term_override():
    """Read the HOD-configured term schedule from DepartmentConfig.

    Expected JSON shape:
        {"year": 2026, "terms": [
            {"num": 1, "start": "2026-01-02", "end": "2026-03-13"}, ...
        ]}

    Returns a list of (num, start_date, end_date) tuples or None if no
    override is set / parse fails. Errors are swallowed deliberately —
    a malformed override should never crash the dashboard."""
    try:
        cfg = DepartmentConfig.query.filter_by(key='term_schedule_override').first()
    except Exception:
        return None
    if not cfg or not cfg.value:
        return None
    try:
        data = json.loads(cfg.value)
        terms = []
        for t in data.get('terms') or []:
            num = int(t['num'])
            start = date.fromisoformat(t['start'])
            end = date.fromisoformat(t['end'])
            if start <= end:
                terms.append((num, start, end))
        return terms or None
    except (ValueError, KeyError, TypeError):
        return None


def _pick_term_from_list(today, terms):
    """Given today's date and a list of (num, start, end) tuples, return
    (term, is_current). `term` is the term containing today, or the most
    recently ended term, or None if no term qualifies. `is_current` is
    True iff today falls inside the returned term's bounds."""
    for term in terms:
        _, start, end = term
        if start <= today <= end:
            return term, True
    ended = [t for t in terms if t[2] <= today]
    if ended:
        return max(ended, key=lambda t: t[2]), False
    return None, False


def _resolve_term_window(now):
    """Decide the (term_start, term_end, label, source) window for `now`.

    Resolution order:
      1. HOD-configured override (DepartmentConfig.term_schedule_override).
      2. MOE published term that contains today → that term's bounds.
      3. MOE most-recently-ended term → its bounds (handles holiday
         windows so "current term" reflects the just-ended term).
      4. Rolling 70-day fallback (DEFAULT_TERM_DAYS) ending at `now`.

    Returns a 4-tuple: (term_start, term_end, label, source) where
    term_start/term_end are timezone-aware datetimes at UTC midnight.
    `source` is one of 'override', 'override_recent', 'moe',
    'moe_recent', 'rolling'."""
    today = now.date()

    override_terms = _load_term_override()
    if override_terms:
        term, is_current = _pick_term_from_list(today, override_terms)
        if term is not None:
            num, start, end = term
            suffix = '' if is_current else ' (just ended)'
            return (
                datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
                datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1),
                f'Term {num} {start.year}{suffix}',
                'override' if is_current else 'override_recent',
            )

    term = moe_current_term(today)
    if term is not None:
        num, start, end = term
        return (
            datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
            datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1),
            f'Term {num} {start.year}',
            'moe',
        )
    recent = moe_most_recent_term(today)
    if recent is not None:
        num, start, end = recent
        return (
            datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
            datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1),
            f'Term {num} {start.year} (just ended)',
            'moe_recent',
        )
    return (
        now - timedelta(days=DEFAULT_TERM_DAYS),
        now,
        f'Last {DEFAULT_TERM_DAYS} days',
        'rolling',
    )


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
        self.term_start, self.term_end, self.term_label, self.term_source = (
            _resolve_term_window(self.now)
        )
        # Prior-term window mirrors the current term's length so trend
        # comparisons are like-for-like.
        term_length = self.term_end - self.term_start
        self.prior_term_start = self.term_start - term_length

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
    """Assignments per weekly bin from term start to term end, plus the
    median weekly rate across all bands for context.

    Bins cover the full term (10 weeks for an MOE term). Weeks that fall
    after `slice_.now` are kept in the chart with count=0 so the planned
    term cadence is visible mid-term."""
    term_asns = [
        a for a in slice_.assignments
        if _aware(a.created_at) and _aware(a.created_at) >= slice_.term_start
    ]
    bins = []
    cursor = slice_.term_start
    while cursor < slice_.term_end:
        end = min(cursor + timedelta(days=7), slice_.term_end)
        n = sum(1 for a in term_asns
                if _aware(a.created_at)
                and cursor <= _aware(a.created_at) < end)
        bins.append({
            'start': cursor.date().isoformat(),
            'end': end.date().isoformat(),
            'count': n,
            'future': cursor > slice_.now,
        })
        cursor = end

    # Rate is computed over elapsed bins only — future weeks shouldn't
    # drag the band's average down.
    elapsed_bins = [b for b in bins if not b['future']]
    band_rate = (
        sum(b['count'] for b in elapsed_bins) / len(elapsed_bins)
        if elapsed_bins else 0
    )

    other_rates = []
    n_elapsed = max(1, len(elapsed_bins))
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
            .filter(Assignment.created_at < slice_.now)
            .all()
        )
        other_rates.append(len(other_asns) / n_elapsed)
    median_across = round(statistics.median(other_rates), 2) if other_rates else 0

    return {
        'bins': bins,
        'band_rate': round(band_rate, 2),
        'median_across_bands': median_across,
        'term_label': slice_.term_label,
        'term_source': slice_.term_source,
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
    for key in ('band_health', 'score_distribution', 'marking_pipeline'):
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
            'end': slice_.term_end.date().isoformat(),
            'days': int((slice_.term_end - slice_.term_start).total_seconds() // 86400),
            'label': slice_.term_label,
            'source': slice_.term_source,
        },
        'band_health': compute_band_health(slice_),
        'dept_goals': compute_dept_goals(slice_, viewer_role, viewer_subjects),
        'score_distribution': compute_score_distribution(slice_),
        'marking_pipeline': compute_marking_pipeline(slice_),
        'assessment_rhythm': compute_assessment_rhythm(slice_),
        'wins_to_share': compute_wins_to_share(slice_),
    }
    payload['low_sample_widgets'] = build_low_sample_list(payload)
    return payload
