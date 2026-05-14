# tests/test_dept_seed_backfill.py
"""Coverage for the Department / DepartmentSubject / TeacherDepartment
tables introduced by the 2026-05-14 multi-dept design.
"""
import pytest
from db import db, Department, DepartmentSubject, TeacherDepartment, Teacher
from db import Assignment
from db import seed_departments, backfill_teacher_departments


def test_department_model_columns(app):
    cols = {c.name for c in Department.__table__.columns}
    assert cols >= {'id', 'name', 'short_name', 'sort_order', 'is_active', 'created_at'}


def test_department_subject_model_columns(app):
    cols = {c.name for c in DepartmentSubject.__table__.columns}
    assert cols >= {'department_id', 'subject_key'}


def test_teacher_department_model_columns(app):
    cols = {c.name for c in TeacherDepartment.__table__.columns}
    assert cols >= {'teacher_id', 'department_id', 'is_lead', 'added_at'}


def test_can_create_and_query_department(app, db_session):
    d = Department(name='Test Dept', short_name='Test', sort_order=99)
    db_session.add(d)
    db_session.commit()

    fetched = Department.query.filter_by(name='Test Dept').first()
    assert fetched is not None
    assert fetched.short_name == 'Test'
    assert fetched.is_active is True

    db_session.delete(fetched)
    db_session.commit()


def test_teacher_department_cascade_on_teacher_delete(app, db_session):
    # Enable SQLite FK enforcement for this connection so ondelete='CASCADE'
    # is actually checked. SQLite ignores FK constraints by default.
    # We turn it OFF again at the end so the pooled connection doesn't break
    # later tests that intentionally insert FK-violating fixtures.
    raw = db_session.connection().connection.connection
    raw.execute('PRAGMA foreign_keys=ON')

    try:
        t = Teacher(id='td-test-1', name='Cascade Teacher', code='CASC1234', role='teacher')
        d = Department(name='Cascade Dept', short_name='Cascade')
        db_session.add_all([t, d])
        db_session.commit()
        db_session.add(TeacherDepartment(teacher_id=t.id, department_id=d.id, is_lead=False))
        db_session.commit()

        db_session.delete(t)
        db_session.commit()

        assert TeacherDepartment.query.filter_by(teacher_id='td-test-1').count() == 0
        assert Department.query.filter_by(name='Cascade Dept').first() is not None
        db_session.delete(Department.query.filter_by(name='Cascade Dept').first())
        db_session.commit()
    finally:
        raw.execute('PRAGMA foreign_keys=OFF')


SEEDED_DEPTS = {
    'Aesthetics and Craft and Technology':
        {'art', 'music', 'design_and_technology', 'nutrition_and_food_science'},
    'English Language and Literature':
        {'english', 'literature_in_english'},
    'Humanities':
        {'geography', 'social_studies', 'history'},
    'Mathematics and Principles of Accounts':
        {'mathematics', 'principles_of_accounts'},
    'Mother Tongue Language':
        {'chinese', 'malay', 'tamil'},
    'Science':
        {'chemistry', 'biology', 'computing', 'physics',
         'lower_secondary_science', 'science'},
}


@pytest.fixture()
def empty_dept_tables(app, db_session):
    db_session.query(TeacherDepartment).delete()
    db_session.query(DepartmentSubject).delete()
    db_session.query(Department).delete()
    db_session.commit()
    yield


def test_seed_inserts_six_departments(app, db_session, empty_dept_tables):
    seed_departments()
    names = {d.name for d in Department.query.all()}
    assert names == set(SEEDED_DEPTS.keys())


def test_seed_inserts_correct_subject_mapping(app, db_session, empty_dept_tables):
    seed_departments()
    for name, expected in SEEDED_DEPTS.items():
        dept = Department.query.filter_by(name=name).first()
        got = {ds.subject_key
               for ds in DepartmentSubject.query.filter_by(department_id=dept.id).all()}
        assert got == expected, f'{name}: got {got}, expected {expected}'


def test_seed_is_idempotent(app, db_session, empty_dept_tables):
    seed_departments()
    first = Department.query.count()
    seed_departments()
    assert Department.query.count() == first


def test_seed_assigns_short_names(app, db_session, empty_dept_tables):
    seed_departments()
    for d in Department.query.all():
        assert d.short_name, f'dept {d.name!r} missing short_name'
        assert len(d.short_name) <= 24


def test_seeded_subject_keys_all_canonical(app, db_session, empty_dept_tables):
    from subjects import SUBJECT_KEYS
    seed_departments()
    for ds in DepartmentSubject.query.all():
        assert ds.subject_key in SUBJECT_KEYS, \
            f'seed inserted unknown subject_key {ds.subject_key!r}'


def test_backfill_tags_teacher_from_assignment_history(app, db_session, empty_dept_tables):
    seed_departments()
    t = Teacher(id='bf-test-1', name='Mr Maths', code='BF010001', role='teacher')
    db_session.add(t)
    db_session.commit()
    db_session.add(Assignment(id='bf-asn-1', classroom_code='BFAS0001',
                              title='Algebra W1', subject='Mathematics',
                              teacher_id=t.id))
    db_session.commit()

    backfill_teacher_departments(force=True)

    maths = Department.query.filter_by(name='Mathematics and Principles of Accounts').first()
    row = TeacherDepartment.query.filter_by(teacher_id=t.id, department_id=maths.id).first()
    assert row is not None
    assert row.is_lead is False


def test_backfill_promotes_hod_to_lead_on_all_their_depts(app, db_session, empty_dept_tables):
    seed_departments()
    hod = Teacher(id='bf-hod-1', name='HOD Lee', code='BFHOD001', role='hod')
    db_session.add(hod)
    db_session.commit()
    db_session.add_all([
        Assignment(id='bf-asn-2', classroom_code='BFAS0002',
                   title='', subject='Mathematics', teacher_id=hod.id),
        Assignment(id='bf-asn-3', classroom_code='BFAS0003',
                   title='', subject='Physics', teacher_id=hod.id),
    ])
    db_session.commit()

    backfill_teacher_departments(force=True)

    rows = TeacherDepartment.query.filter_by(teacher_id=hod.id).all()
    assert len(rows) == 2
    assert all(r.is_lead for r in rows)


def test_backfill_skips_freeform_subject(app, db_session, empty_dept_tables):
    seed_departments()
    t = Teacher(id='bf-test-2', name='Ms Freeform', code='BFFREE01', role='teacher')
    db_session.add(t)
    db_session.commit()
    db_session.add(Assignment(id='bf-asn-4', classroom_code='BFAS0004',
                              title='', subject='Project Work', teacher_id=t.id))
    db_session.commit()

    backfill_teacher_departments(force=True)

    assert TeacherDepartment.query.filter_by(teacher_id=t.id).count() == 0
