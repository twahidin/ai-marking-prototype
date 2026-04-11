"""Seed data for DEMO_MODE + DEPT_MODE showcase."""
import uuid
import secrets
import string
from datetime import datetime, timezone, timedelta
import random


def seed_demo_department(db, Teacher, Class, TeacherClass, Assignment, Student, Submission, DepartmentConfig):
    """Populate DB with fake data for demo+dept mode. Idempotent -- skips if data exists."""
    # Check if already seeded
    if Teacher.query.filter_by(role='hod').first():
        return

    # Create HOD
    hod = Teacher(id=str(uuid.uuid4()), name='Dr. Sarah Lim', code='DEMOHOD1', role='hod')
    db.session.add(hod)

    # Create teachers
    teachers = [
        Teacher(id=str(uuid.uuid4()), name='Ms. Chen Wei Ling', code='DEMO0001', role='teacher'),
        Teacher(id=str(uuid.uuid4()), name='Mr. Rahman bin Ismail', code='DEMO0002', role='teacher'),
        Teacher(id=str(uuid.uuid4()), name='Ms. Tan Mei Xin', code='DEMO0003', role='teacher'),
    ]
    for t in teachers:
        db.session.add(t)

    # Create classes
    classes = [
        Class(id=str(uuid.uuid4()), name='3A', level='Mathematics'),
        Class(id=str(uuid.uuid4()), name='3B', level='Mathematics'),
        Class(id=str(uuid.uuid4()), name='4A', level='Mathematics'),
    ]
    for c in classes:
        db.session.add(c)

    # Assign teachers to classes
    assignments_map = [
        (teachers[0].id, classes[0].id),  # Chen -> 3A
        (teachers[0].id, classes[1].id),  # Chen -> 3B
        (teachers[1].id, classes[2].id),  # Rahman -> 4A
        (teachers[2].id, classes[1].id),  # Tan -> 3B
        (teachers[2].id, classes[2].id),  # Tan -> 4A
    ]
    for tid, cid in assignments_map:
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
