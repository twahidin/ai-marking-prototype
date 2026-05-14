"""Coverage for the 2026-05-14 canonical-subject taxonomy update.

Spec: docs/superpowers/specs/2026-05-14-teacher-department-tags-design.md
"""
from subjects import (
    SUBJECTS, SUBJECT_KEYS, KEY_TO_DISPLAY, resolve_subject_key,
)


def test_new_subjects_present():
    for key, display in [
        ('science', 'Science'),
        ('computing', 'Computing'),
        ('design_and_technology', 'Design and Technology'),
        ('principles_of_accounts', 'Principles of Accounts'),
    ]:
        assert key in SUBJECT_KEYS, f'{key} missing from SUBJECT_KEYS'
        assert KEY_TO_DISPLAY[key] == display


def test_hindi_removed():
    assert 'hindi' not in SUBJECT_KEYS


def test_science_alias_resolves_to_science_not_lss():
    assert resolve_subject_key('science') == 'science'
    assert resolve_subject_key('Sci') == 'science'
    assert resolve_subject_key('SCIENCE') == 'science'


def test_lss_aliases_no_longer_match_bare_science():
    assert resolve_subject_key('general science') != 'lower_secondary_science'
    assert resolve_subject_key('lower secondary science') == 'lower_secondary_science'
    assert resolve_subject_key('lower sec science') == 'lower_secondary_science'
    assert resolve_subject_key('lss') == 'lower_secondary_science'


def test_computing_aliases():
    for alias in ('computing', 'computer science', 'cs', 'comp'):
        assert resolve_subject_key(alias) == 'computing', f'failed on {alias!r}'


def test_design_and_technology_aliases():
    for alias in ('d&t', 'dnt', 'design technology',
                  'design & technology', 'design and technology'):
        assert resolve_subject_key(alias) == 'design_and_technology', f'failed on {alias!r}'


def test_principles_of_accounts_aliases():
    for alias in ('poa', 'principles of accounts', 'accounts', 'accounting'):
        assert resolve_subject_key(alias) == 'principles_of_accounts', f'failed on {alias!r}'


def test_subjects_alphabetised_by_display():
    displays = [s['display'] for s in SUBJECTS]
    assert displays == sorted(displays), 'SUBJECTS must stay alphabetised by display'
