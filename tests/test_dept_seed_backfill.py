# tests/test_dept_seed_backfill.py
"""Coverage for the Department / DepartmentSubject / TeacherDepartment
tables introduced by the 2026-05-14 multi-dept design.
"""
from db import db, Department, DepartmentSubject, TeacherDepartment, Teacher


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
