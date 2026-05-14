"""Singapore MOE school term dates for primary & secondary schools.

Source: https://www.moe.gov.sg/calendar (official MOE schedule).
Update annually when MOE publishes next year's calendar — usually
around Oct/Nov of the preceding year.

Used by `dept_insights_widgets.BandSlice` to align the "current term"
window to real MOE term boundaries instead of a rolling 70-day default.
On dates that fall in a school holiday or outside any year defined
here, the rolling fallback applies — see BandSlice.__init__.

Department-level overrides (set by HOD in settings) take precedence
over this file. See `DepartmentConfig.term_schedule_override`.
"""
from datetime import date, datetime, timezone


# Each year maps to a list of (term_number, start_date, end_date) tuples
# in chronological order. end_date is INCLUSIVE — the last instructional
# day of the term.
MOE_TERMS = {
    2026: [
        (1, date(2026, 1, 2),  date(2026, 3, 13)),
        (2, date(2026, 3, 23), date(2026, 5, 29)),
        (3, date(2026, 6, 29), date(2026, 9, 4)),
        (4, date(2026, 9, 14), date(2026, 11, 20)),
    ],
}


def current_term(today=None):
    """Return (term_number, start_date, end_date) for the MOE term that
    contains `today`, or None if `today` falls in a school holiday or
    outside any year defined in MOE_TERMS.

    `today` defaults to today's date in UTC."""
    if today is None:
        today = datetime.now(timezone.utc).date()
    terms = MOE_TERMS.get(today.year)
    if not terms:
        return None
    for term in terms:
        _, start, end = term
        if start <= today <= end:
            return term
    return None


def most_recent_term(today=None):
    """Return the most recent term that ended on or before `today`,
    or None if no year defined in MOE_TERMS qualifies. Used as the
    fallback when `today` is in a holiday window so the dashboard
    still reflects the just-ended term rather than a rolling
    70-day window cutting through the prior holiday."""
    if today is None:
        today = datetime.now(timezone.utc).date()
    candidates = []
    for _year, terms in MOE_TERMS.items():
        for term in terms:
            _, _start, end = term
            if end <= today:
                candidates.append(term)
    if not candidates:
        return None
    return max(candidates, key=lambda t: t[2])
