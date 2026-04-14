"""Seed data for DEMO_MODE + DEPT_MODE showcase."""
import uuid
import secrets
import string
from datetime import datetime, timezone, timedelta
import random


def seed_demo_department(db, Teacher, Class, TeacherClass, Assignment, Student, Submission):
    """Populate DB with fake data for demo+dept mode. Idempotent -- skips if data exists."""
    import logging
    logger = logging.getLogger(__name__)
    # Check if already seeded
    existing_hod = Teacher.query.filter_by(role='hod').first()
    if existing_hod:
        logger.info(f'Seed: HOD already exists ({existing_hod.name}), checking data completeness')
        # Verify seed data is complete (not just HOD from a failed prior run)
        if Submission.query.count() > 0:
            logger.info(f'Seed: {Submission.query.count()} submissions exist, skipping seed')
            return
        logger.info('Seed: HOD exists but no submissions — re-seeding data (keeping HOD)')
    else:
        logger.info('Seed: No HOD found, creating seed data')

    # Create HOD (skip if already exists)
    if not existing_hod:
        hod = Teacher(id=str(uuid.uuid4()), name='Dr. Sarah Lim', code='DEMOHOD1', role='hod')
        db.session.add(hod)

    # Create teachers (skip if they exist by code)
    teacher_defs = [
        ('Ms. Chen Wei Ling', 'DEMO0001', 'teacher'),
        ('Mr. Rahman bin Ismail', 'DEMO0002', 'teacher'),
        ('Ms. Tan Mei Xin', 'DEMO0003', 'teacher'),
        ('Mr. David Ng', 'DEMO0004', 'subject_head'),
        ('Ms. Priya Nair', 'DEMO0005', 'lead'),
        ('Mr. Ahmad Fauzi', 'DEMO0006', 'manager'),
    ]
    teachers = []
    for name, code, role in teacher_defs:
        t = Teacher.query.filter_by(code=code).first()
        if not t:
            t = Teacher(id=str(uuid.uuid4()), name=name, code=code, role=role)
            db.session.add(t)
        teachers.append(t)

    # Create classes (skip if they exist by name+level)
    class_defs = [
        ('3A', 'Secondary 3'),
        ('3B', 'Secondary 3'),
        ('4A', 'Secondary 4'),
    ]
    classes = []
    for name, level in class_defs:
        c = Class.query.filter_by(name=name, level=level).first()
        if not c:
            c = Class(id=str(uuid.uuid4()), name=name, level=level)
            db.session.add(c)
        classes.append(c)

    # Assign teachers to classes
    assignments_map = [
        (teachers[0].id, classes[0].id),  # Chen -> 3A
        (teachers[0].id, classes[1].id),  # Chen -> 3B
        (teachers[1].id, classes[2].id),  # Rahman -> 4A
        (teachers[2].id, classes[1].id),  # Tan -> 3B
        (teachers[2].id, classes[2].id),  # Tan -> 4A
    ]
    for tid, cid in assignments_map:
        if not TeacherClass.query.filter_by(teacher_id=tid, class_id=cid).first():
            db.session.add(TeacherClass(teacher_id=tid, class_id=cid))

    # Student names pool
    first_names = [
        'Alex', 'Jordan', 'Sam', 'Casey', 'Riley', 'Morgan', 'Taylor', 'Drew',
        'Avery', 'Quinn', 'Blake', 'Cameron', 'Dakota', 'Emery', 'Finley',
        'Harper', 'Jamie', 'Kendall', 'Logan', 'Peyton',
    ]
    last_names = [
        'Lim', 'Tan', 'Ng', 'Lee', 'Wong', 'Chen', 'Goh', 'Chua',
        'Ong', 'Koh', 'Teo', 'Sim', 'Ho', 'Yeo', 'Poh',
        'Foo', 'Soh', 'Toh', 'Ang', 'Wee',
    ]

    rng = random.Random(42)  # Deterministic seed for reproducible results

    student_counts = {classes[0].id: 15, classes[1].id: 18, classes[2].id: 12}

    for cls in classes:
        count = student_counts[cls.id]
        for i in range(count):
            student = Student(
                class_id=cls.id,
                index_number=str(i + 1).zfill(2),
                name=f'{rng.choice(first_names)} {rng.choice(last_names)}',
            )
            db.session.add(student)

    db.session.flush()  # Get student IDs

    # Create assignments and fake results
    teacher_for_class = {
        classes[0].id: teachers[0].id,  # 3A -> Chen
        classes[1].id: teachers[0].id,  # 3B -> Chen
        classes[2].id: teachers[1].id,  # 4A -> Rahman
    }

    assignment_titles = ['Mid-Year Exam', 'Quiz 3']

    for cls in classes:
        for title in assignment_titles:
            num_questions = 8 if 'Exam' in title else 5
            marks_per_q = 5

            asn = Assignment(
                id=str(uuid.uuid4()),
                classroom_code=''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6)),
                title=title,
                subject='Mathematics',
                assign_type='short_answer',
                scoring_mode='marks',
                total_marks=str(num_questions * marks_per_q),
                provider='anthropic',
                model='claude-haiku-4-5-20251001',
                show_results=True,
                class_id=cls.id,
                teacher_id=teacher_for_class[cls.id],
            )
            db.session.add(asn)
            db.session.flush()

            # Create fake submissions for ~90% of students
            students = Student.query.filter_by(class_id=cls.id).all()
            for student in students:
                if rng.random() < 0.1:  # 10% no submission
                    continue

                # Generate realistic score distribution (mean 75%, std 12%)
                score_pct = max(0, min(100, rng.gauss(75, 12)))
                total_marks_val = num_questions * marks_per_q
                target_score = score_pct / 100 * total_marks_val

                questions = []
                remaining = target_score
                for qi in range(num_questions):
                    if qi < num_questions - 1:
                        awarded = max(0, min(marks_per_q, round(remaining / (num_questions - qi) + rng.gauss(0, 0.8))))
                    else:
                        awarded = max(0, min(marks_per_q, round(remaining)))
                    remaining -= awarded

                    status = 'correct' if awarded == marks_per_q else 'partial' if awarded > 0 else 'incorrect'
                    topic = rng.choice(['algebra', 'geometry', 'fractions', 'word problems', 'equations'])
                    questions.append({
                        'question_number': str(qi + 1),
                        'marks_awarded': awarded,
                        'marks_total': marks_per_q,
                        'status': status,
                        'feedback': 'Well done!' if status == 'correct' else f'Review {topic} concepts.',
                        'recommended_action': 'Keep up the good work.' if status == 'correct' else 'Practice more problems in this area.',
                    })

                result = {'questions': questions}

                sub = Submission(
                    student_id=student.id,
                    assignment_id=asn.id,
                    status='done',
                    submitted_at=datetime.now(timezone.utc) - timedelta(days=rng.randint(1, 14)),
                    marked_at=datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 7)),
                )
                sub.set_result(result)
                db.session.add(sub)

    db.session.commit()
