import os
import json
import base64
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any
from flask_sqlalchemy import SQLAlchemy

try:
    from cryptography.fernet import Fernet, InvalidToken
    FERNET_AVAILABLE = True
except ImportError:
    FERNET_AVAILABLE = False

logger = logging.getLogger(__name__)

db = SQLAlchemy()


# UP-37: typed status set for `Submission.status`. The `str` mixin lets
# Jinja and SQLAlchemy treat instances exactly like the raw strings they
# replace — `sub.status == 'done'` keeps working, and so does
# `sub.status = SubmissionStatus.DONE`. Migrate progressively; the index
# is fine because the persisted values are identical.
class SubmissionStatus(str, Enum):
    PENDING = 'pending'
    EXTRACTING = 'extracting'
    PREVIEW = 'preview'
    PROCESSING = 'processing'
    DONE = 'done'
    ERROR = 'error'


def utc(dt: datetime | None) -> datetime | None:
    """UP-37: coerce a naive `datetime` to UTC, leaving aware datetimes
    untouched. Centralises the `if dt.tzinfo is None: dt = dt.replace(
    tzinfo=timezone.utc)` pattern that's duplicated ~9× across app.py.
    Returns `None` unchanged so callers can drop dead `if dt is None`
    guards around the coercion.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _get_fernet():
    """Derive a Fernet key from FLASK_SECRET_KEY for encrypting API keys at rest."""
    if not FERNET_AVAILABLE:
        return None
    key = os.getenv('FLASK_SECRET_KEY', '')
    if not key:
        # Fall back to DB-stored secret key (auto-generated on first boot)
        try:
            cfg = DepartmentConfig.query.filter_by(key='flask_secret_key').first()
            if cfg and cfg.value:
                key = cfg.value
        except Exception:
            pass
    if not key:
        return None
    derived = hashlib.sha256(key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _migrate_add_columns(app):
    """Add missing columns to existing tables (create_all only creates new tables)."""
    from sqlalchemy import text, inspect
    with app.app_context():
        inspector = inspect(db.engine)
        if 'submissions' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('submissions')]
            if 'script_pages_json' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN script_pages_json TEXT'))
                db.session.commit()
                logger.info('Added script_pages_json column to submissions table')
            if 'extracted_text_json' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN extracted_text_json TEXT'))
                db.session.commit()
                logger.info('Added extracted_text_json column to submissions table')
            if 'student_text_json' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN student_text_json TEXT'))
                db.session.commit()
                logger.info('Added student_text_json column to submissions table')
            if 'student_amended' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN student_amended BOOLEAN DEFAULT FALSE'))
                db.session.commit()
                logger.info('Added student_amended column to submissions table')
            if 'draft_number' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN draft_number INTEGER DEFAULT 1 NOT NULL'))
                db.session.commit()
                logger.info('Added draft_number column to submissions table')
            if 'is_final' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN is_final BOOLEAN DEFAULT TRUE NOT NULL'))
                db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_submissions_is_final ON submissions (is_final)'))
                db.session.commit()
                db.session.execute(text('UPDATE submissions SET draft_number = 1 WHERE draft_number IS NULL'))
                db.session.execute(text('UPDATE submissions SET is_final = TRUE WHERE is_final IS NULL'))
                db.session.commit()
                logger.info('Added is_final column to submissions table and backfilled defaults')
            if 'feedback_opened_at' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN feedback_opened_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added feedback_opened_at column to submissions table')
            if 'correction_submitted_at' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN correction_submitted_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added correction_submitted_at column to submissions table')
            if 'categorisation_status' not in columns:
                db.session.execute(text("ALTER TABLE submissions ADD COLUMN categorisation_status VARCHAR(20) DEFAULT 'pending'"))
                db.session.commit()
                logger.info('Added categorisation_status column to submissions table')
            if 'usage_json' not in columns:
                # UP-12: per-AI-call usage log. Legacy rows stay NULL —
                # readers tolerate that. New marking runs append entries.
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN usage_json TEXT'))
                db.session.commit()
                logger.info('Added usage_json column to submissions table')
        if 'students' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('students')]
            if 'class_id' not in columns:
                db.session.execute(text("ALTER TABLE students ADD COLUMN class_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added class_id column to students table')
            if 'assignment_id' not in columns:
                db.session.execute(text("ALTER TABLE students ADD COLUMN assignment_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added assignment_id column to students table')
            else:
                # Ensure assignment_id is nullable (students belong to classes, not assignments)
                col_info = next((c for c in inspector.get_columns('students') if c['name'] == 'assignment_id'), None)
                if col_info and not col_info.get('nullable', True):
                    db.session.execute(text("ALTER TABLE students ALTER COLUMN assignment_id DROP NOT NULL"))
                    db.session.commit()
                    logger.info('Made assignment_id nullable on students table')
            if 'created_at' not in columns:
                # Used by the missed-submissions widget to ignore late joiners
                # for assignments that pre-date them. Backfill legacy rows
                # with the parent class's created_at — the best proxy we have
                # for "when this student joined the class". Per the schema-
                # evolution policy: lazy-fill via the model default, plus
                # this one-shot idempotent backfill on every boot.
                db.session.execute(text('ALTER TABLE students ADD COLUMN created_at TIMESTAMP'))
                db.session.commit()
                db.session.execute(text(
                    'UPDATE students SET created_at = ('
                    'SELECT created_at FROM classes WHERE classes.id = students.class_id'
                    ') WHERE created_at IS NULL AND class_id IS NOT NULL'
                ))
                db.session.execute(text(
                    "UPDATE students SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
                ))
                db.session.commit()
                logger.info('Added students.created_at and backfilled from classes')
        if 'teachers' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('teachers')]
            if 'is_active' not in columns:
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
                db.session.commit()
                logger.info('Added is_active column to teachers table')
            # Widen role column for new roles (subject_head, lead, manager)
            for col in inspector.get_columns('teachers'):
                if col['name'] == 'role' and hasattr(col['type'], 'length') and col['type'].length and col['type'].length < 20:
                    try:
                        db.session.execute(text("ALTER TABLE teachers ALTER COLUMN role TYPE VARCHAR(20)"))
                        db.session.commit()
                        logger.info('Widened role column to VARCHAR(20)')
                    except Exception:
                        db.session.rollback()
                    break
        if 'assignments' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('assignments')]
            if 'title' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN title VARCHAR(300) DEFAULT ''"))
                db.session.commit()
                logger.info('Added title column to assignments table')
            # One-shot backfill: any assignment with no title gets its
            # subject as a sensible fallback (or "Assignment <code>" if
            # the subject is also empty). This keeps the new "Assignment"
            # row in the PDF generator from showing a dash for legacy
            # rows. Idempotent — guarded by WHERE title IS NULL OR title=''.
            try:
                db.session.execute(text(
                    "UPDATE assignments SET title = subject "
                    "WHERE (title IS NULL OR title = '') "
                    "AND subject IS NOT NULL AND subject != ''"
                ))
                db.session.execute(text(
                    "UPDATE assignments SET title = 'Assignment ' || classroom_code "
                    "WHERE title IS NULL OR title = ''"
                ))
                db.session.commit()
            except Exception as _e:
                db.session.rollback()
                logger.warning(f'Assignment.title backfill skipped: {_e}')
            if 'class_id' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN class_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added class_id column to assignments table')
            if 'teacher_id' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN teacher_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added teacher_id column to assignments table')
            if 'allow_drafts' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN allow_drafts BOOLEAN DEFAULT FALSE NOT NULL'))
                db.session.commit()
                logger.info('Added allow_drafts column to assignments table')
            if 'max_drafts' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN max_drafts INTEGER DEFAULT 3 NOT NULL'))
                db.session.commit()
                logger.info('Added max_drafts column to assignments table')
            if 'last_edited_at' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN last_edited_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added last_edited_at column to assignments table')
            if 'needs_remark' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN needs_remark BOOLEAN DEFAULT FALSE NOT NULL'))
                db.session.commit()
                logger.info('Added needs_remark column to assignments table')
            if 'exemplar_analysis_json' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN exemplar_analysis_json TEXT'))
                db.session.commit()
                logger.info('Added exemplar_analysis_json column to assignments table')
            if 'exemplar_analyzed_at' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN exemplar_analyzed_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added exemplar_analyzed_at column to assignments table')
            # subject_family was a denormalised AI-classified slug.
            # Retired: assignments now key calibration directly on the
            # canonical Assignment.subject string from the dropdown
            # (subjects.py). Idempotent drop — safe to redeploy.
            if 'subject_family' in columns:
                try:
                    db.session.execute(text(
                        'ALTER TABLE assignments DROP COLUMN subject_family'
                    ))
                    db.session.commit()
                    logger.info('Dropped legacy subject_family column from assignments table')
                except Exception as _e:
                    db.session.rollback()
                    logger.warning(f'assignments DROP subject_family skipped: {_e}')
            if 'pinyin_mode' not in columns:
                # Default 'off' so legacy rows don't suddenly start emitting
                # pinyin on next render. Teachers opt-in per assignment via
                # the form dropdown — only meaningful for chinese subject.
                db.session.execute(text(
                    "ALTER TABLE assignments ADD COLUMN pinyin_mode VARCHAR(10) "
                    "DEFAULT 'off' NOT NULL"
                ))
                db.session.commit()
                logger.info('Added pinyin_mode column to assignments table')

        if 'assignment_bank' in inspector.get_table_names():
            ab_cols = {c['name'] for c in inspector.get_columns('assignment_bank')}
            ensure_ab = [
                ('provider', "VARCHAR(50) DEFAULT ''"),
                ('model', "VARCHAR(100) DEFAULT ''"),
                ('pinyin_mode', "VARCHAR(10) DEFAULT 'off'"),
                ('show_results', 'BOOLEAN DEFAULT TRUE'),
                ('allow_drafts', 'BOOLEAN DEFAULT FALSE NOT NULL'),
                ('max_drafts', 'INTEGER DEFAULT 3 NOT NULL'),
                ('answer_key_amendments', 'TEXT'),
            ]
            for col, ddl in ensure_ab:
                if col not in ab_cols:
                    try:
                        db.session.execute(text(f'ALTER TABLE assignment_bank ADD COLUMN {col} {ddl}'))
                        db.session.commit()
                        logger.info(f'Added {col} column to assignment_bank table')
                    except Exception:
                        db.session.rollback()
                        logger.exception('assignment_bank ALTER ADD %s failed', col)
            try:
                db.session.execute(text(
                    "UPDATE assignment_bank SET provider = '' WHERE provider IS NULL"
                ))
                db.session.execute(text(
                    "UPDATE assignment_bank SET model = '' WHERE model IS NULL"
                ))
                db.session.execute(text(
                    "UPDATE assignment_bank SET pinyin_mode = 'off' "
                    "WHERE pinyin_mode IS NULL OR pinyin_mode = ''"
                ))
                db.session.execute(text(
                    "UPDATE assignment_bank SET show_results = TRUE WHERE show_results IS NULL"
                ))
                db.session.execute(text(
                    "UPDATE assignment_bank SET allow_drafts = FALSE WHERE allow_drafts IS NULL"
                ))
                db.session.execute(text(
                    "UPDATE assignment_bank SET max_drafts = 3 WHERE max_drafts IS NULL"
                ))
                db.session.commit()
            except Exception as _e:
                db.session.rollback()
                logger.warning(f'assignment_bank backfill skipped: {_e}')

            # One-shot heal: the older push-amendments-to-bank flow wrote
            # plain UTF-8 text bytes over the existing PDF in `answer_key`,
            # making the bank preview's PDF viewer error with "Unsupported
            # document format". Detect those corrupted rows (non-PDF non-image
            # bytes in `answer_key` AND empty `answer_key_amendments`), move
            # the text into `answer_key_amendments`, and clear `answer_key`.
            # Idempotent: re-running on already-healed rows is a no-op since
            # cleared answer_keys are NULL and amendments is non-empty.
            try:
                corrupted_q = db.session.execute(text(
                    "SELECT id, answer_key, answer_key_amendments FROM assignment_bank "
                    "WHERE answer_key IS NOT NULL "
                    "AND (answer_key_amendments IS NULL OR answer_key_amendments = '')"
                )).fetchall()
                heal_count = 0
                for row in corrupted_q:
                    blob = row.answer_key
                    if not blob:
                        continue
                    head = bytes(blob[:16])
                    is_pdf = head.startswith(b'%PDF')
                    is_jpg = head.startswith(b'\xff\xd8\xff')
                    is_png = head.startswith(b'\x89PNG\r\n\x1a\n')
                    is_gif = head[:6] in (b'GIF87a', b'GIF89a')
                    is_webp = head.startswith(b'RIFF') and head[8:12] == b'WEBP'
                    is_heic = len(head) >= 12 and head[4:8] == b'ftyp'
                    if is_pdf or is_jpg or is_png or is_gif or is_webp or is_heic:
                        continue
                    try:
                        text_val = bytes(blob).decode('utf-8')
                    except UnicodeDecodeError:
                        continue
                    db.session.execute(
                        text("UPDATE assignment_bank SET answer_key = NULL, "
                             "answer_key_amendments = :amendments WHERE id = :id"),
                        {'amendments': text_val, 'id': row.id},
                    )
                    heal_count += 1
                if heal_count:
                    db.session.commit()
                    logger.info(
                        'assignment_bank heal: moved text-only answer_key bytes to '
                        'answer_key_amendments on %d row(s)', heal_count)
            except Exception as _heal_err:
                db.session.rollback()
                logger.warning(f'assignment_bank heal skipped: {_heal_err}')

        # feedback_edit ensure-list. The table may exist on prod from
        # an older deploy with a partial column set; SELECTs blow up with
        # "column does not exist" if the model references a column the
        # table is missing. Single ensure-list with every column the
        # current model SELECTs/INSERTs. subject_family + subject_bucket
        # are NOT in this list — they were dropped (see below) when
        # calibration moved to keying on assignments.subject directly.
        if 'feedback_edit' in inspector.get_table_names():
            fe_cols = {c['name'] for c in inspector.get_columns('feedback_edit')}
            # Rename legacy column theme_key → mistake_type on existing DBs.
            # Drop the lookup index first; it references the column name and
            # would prevent the rename on some backends. Recreated below
            # after the rest of the feedback_edit migration runs.
            if 'theme_key' in fe_cols and 'mistake_type' not in fe_cols:
                try:
                    db.session.execute(text('DROP INDEX IF EXISTS ix_feedback_edit_lookup'))
                    db.session.execute(text(
                        'ALTER TABLE feedback_edit RENAME COLUMN theme_key TO mistake_type'
                    ))
                    db.session.commit()
                    logger.info('Renamed feedback_edit.theme_key → mistake_type')
                    # Re-inspect with a fresh Inspector — SQLAlchemy caches
                    # column metadata, so reusing `inspector` returns the
                    # pre-rename column list and the ensure-list below tries
                    # to ADD a column that already exists.
                    inspector = inspect(db.engine)
                    fe_cols = {c['name'] for c in inspector.get_columns('feedback_edit')}
                except Exception:
                    db.session.rollback()
                    logger.exception('feedback_edit RENAME theme_key → mistake_type failed')
                # Recreate the lookup index against the new column name.
                # Idempotent — also runs unconditionally if subject_family
                # purge path below fires, so harmless either way.
                try:
                    db.session.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_feedback_edit_lookup '
                        'ON feedback_edit (edited_by, active, mistake_type)'
                    ))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.exception('feedback_edit lookup index recreate after rename failed')

            ensure_fe = [
                ('mistake_type', 'VARCHAR(64)'),
                # promoted_by, promoted_at, scope removed from ORM and dropped by
                # _migrate_drop_subject_standards. Do NOT re-add them here.
                ('propagation_status', "VARCHAR(20) DEFAULT 'none' NOT NULL"),
                ('propagated_to', "TEXT DEFAULT '[]' NOT NULL"),
                ('propagated_at', 'TIMESTAMP'),
                ('rubric_version', "VARCHAR(64) DEFAULT '' NOT NULL"),
                ('mistake_pattern', 'VARCHAR(80)'),
                ('correction_principle', 'VARCHAR(300)'),
                ('transferability', 'VARCHAR(10)'),
            ]
            for col, ddl in ensure_fe:
                if col not in fe_cols:
                    try:
                        db.session.execute(text(f'ALTER TABLE feedback_edit ADD COLUMN {col} {ddl}'))
                        db.session.commit()
                        logger.info(f'Added {col} column to feedback_edit table')
                    except Exception:
                        db.session.rollback()
                        logger.exception('feedback_edit ALTER ADD %s failed', col)

            # Widen mistake_type column for richer per-subject taxonomy keys
            # (longest key is 42 chars; bumped to 64 for headroom).
            for col in inspector.get_columns('feedback_edit'):
                if col['name'] == 'mistake_type' and hasattr(col['type'], 'length') and col['type'].length and col['type'].length < 64:
                    try:
                        db.session.execute(text('ALTER TABLE feedback_edit ALTER COLUMN mistake_type TYPE VARCHAR(64)'))
                        db.session.commit()
                        logger.info('Widened feedback_edit.mistake_type to VARCHAR(64)')
                    except Exception:
                        db.session.rollback()
                    break

            # One-shot purge of legacy calibration data, then drop the
            # subject_family / subject_bucket columns. The user explicitly
            # opted into clearing the calibration corpus when migrating to
            # subject-string-keyed retrieval — old rows can't be reliably
            # remapped (subject_family was AI-classified, subject_bucket
            # was substring-keyword-derived; neither equals the canonical
            # Assignment.subject string).
            fe_cols_after = {c['name'] for c in inspector.get_columns('feedback_edit')}
            if 'subject_family' in fe_cols_after or 'subject_bucket' in fe_cols_after:
                try:
                    db.session.execute(text('DELETE FROM feedback_edit'))
                    db.session.commit()
                    logger.info('Purged feedback_edit (subject taxonomy migration)')
                except Exception as _e:
                    db.session.rollback()
                    logger.warning(f'feedback_edit purge skipped: {_e}')
                # Drop indexes that reference the columns first so the
                # column drops don't fail on a column-still-in-index error.
                for idx in (
                    'ix_feedback_edit_lookup',
                    'ix_feedback_edit_bucket',
                    'ix_feedback_edit_subject_family',
                    'ix_feedback_edit_subject_bucket',
                ):
                    try:
                        db.session.execute(text(f'DROP INDEX IF EXISTS {idx}'))
                        db.session.commit()
                    except Exception as _e:
                        db.session.rollback()
                        logger.warning(f'feedback_edit DROP INDEX {idx} skipped: {_e}')
                for col in ('subject_family', 'subject_bucket'):
                    if col in fe_cols_after:
                        try:
                            db.session.execute(text(
                                f'ALTER TABLE feedback_edit DROP COLUMN {col}'
                            ))
                            db.session.commit()
                            logger.info(f'Dropped legacy {col} column from feedback_edit')
                        except Exception as _e:
                            db.session.rollback()
                            logger.warning(f'feedback_edit DROP COLUMN {col} skipped: {_e}')
                # Recreate the lookup index on the new key shape.
                try:
                    db.session.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_feedback_edit_lookup '
                        'ON feedback_edit (edited_by, active, mistake_type)'
                    ))
                    db.session.commit()
                except Exception as _e:
                    db.session.rollback()
                    logger.warning(f'feedback_edit lookup index create skipped: {_e}')

        # marking_principles_cache rekey. Old: `subject_family` slug
        # (UNIQUE NOT NULL). New: `subject` (the canonical display string
        # from the assignment dropdown — case-insensitive matching is in
        # the SQL). The user opted into clearing the principles cache
        # during this migration; cleanest path is to drop the legacy
        # table entirely and let SQLAlchemy's create_all rebuild it on
        # the next ensure pass. SQLite won't DROP COLUMN on a UNIQUE
        # column without a full table rebuild — drop+recreate is simpler.
        if 'marking_principles_cache' in inspector.get_table_names():
            mpc_cols = {c['name'] for c in inspector.get_columns('marking_principles_cache')}
            if 'subject_family' in mpc_cols:
                try:
                    db.session.execute(text('DROP TABLE marking_principles_cache'))
                    db.session.commit()
                    logger.info('Dropped legacy marking_principles_cache table (subject taxonomy migration)')
                except Exception as _e:
                    db.session.rollback()
                    logger.warning(f'marking_principles_cache DROP TABLE skipped: {_e}')
                # Recreate the table with the current model schema.
                try:
                    MarkingPrinciplesCache.__table__.create(db.engine)
                    logger.info('Recreated marking_principles_cache with new schema')
                except Exception as _e:
                    logger.warning(f'marking_principles_cache recreate skipped: {_e}')

        # categorisation_correction: drop subject_family — no replacement
        # needed, callers JOIN on assignment_id -> assignments.subject.
        if 'categorisation_correction' in inspector.get_table_names():
            cc_cols = {c['name'] for c in inspector.get_columns('categorisation_correction')}
            if 'subject_family' in cc_cols:
                try:
                    db.session.execute(text('DELETE FROM categorisation_correction'))
                    db.session.commit()
                    logger.info('Purged categorisation_correction (subject taxonomy migration)')
                except Exception as _e:
                    db.session.rollback()
                    logger.warning(f'categorisation_correction purge skipped: {_e}')
                try:
                    db.session.execute(text(
                        'DROP INDEX IF EXISTS ix_cat_corr_assignment_subject'
                    ))
                    db.session.commit()
                except Exception as _e:
                    db.session.rollback()
                try:
                    db.session.execute(text(
                        'ALTER TABLE categorisation_correction DROP COLUMN subject_family'
                    ))
                    db.session.commit()
                    logger.info('Dropped legacy subject_family column from categorisation_correction')
                except Exception as _e:
                    db.session.rollback()
                    logger.warning(f'categorisation_correction DROP subject_family skipped: {_e}')

            # Rename legacy theme_key columns → mistake_type on existing DBs.
            cc_cols = {c['name'] for c in inspector.get_columns('categorisation_correction')}
            for old, new in (('original_theme_key', 'original_mistake_type'),
                             ('corrected_theme_key', 'corrected_mistake_type')):
                if old in cc_cols and new not in cc_cols:
                    try:
                        db.session.execute(text(
                            f'ALTER TABLE categorisation_correction RENAME COLUMN {old} TO {new}'
                        ))
                        db.session.commit()
                        logger.info(f'Renamed categorisation_correction.{old} → {new}')
                    except Exception:
                        db.session.rollback()
                        logger.exception(
                            'categorisation_correction RENAME %s → %s failed', old, new,
                        )

            # Widen mistake_type columns for richer per-subject taxonomy.
            for col_name in ('original_mistake_type', 'corrected_mistake_type'):
                for col in inspector.get_columns('categorisation_correction'):
                    if col['name'] == col_name and hasattr(col['type'], 'length') and col['type'].length and col['type'].length < 64:
                        try:
                            db.session.execute(text(
                                f'ALTER TABLE categorisation_correction ALTER COLUMN {col_name} TYPE VARCHAR(64)'
                            ))
                            db.session.commit()
                            logger.info(f'Widened categorisation_correction.{col_name} to VARCHAR(64)')
                        except Exception:
                            db.session.rollback()
                        break

        # subject_standard: rename legacy theme_key → mistake_type column.
        if 'subject_standard' in inspector.get_table_names():
            ss_cols = {c['name'] for c in inspector.get_columns('subject_standard')}
            if 'theme_key' in ss_cols and 'mistake_type' not in ss_cols:
                try:
                    db.session.execute(text(
                        'ALTER TABLE subject_standard RENAME COLUMN theme_key TO mistake_type'
                    ))
                    db.session.commit()
                    logger.info('Renamed subject_standard.theme_key → mistake_type')
                except Exception:
                    db.session.rollback()
                    logger.exception('subject_standard RENAME theme_key → mistake_type failed')

        # exemplar_analysis_log: ensure superseded_at column exists for
        # tables created before the column was added to the model. New
        # tables get it via create_all automatically.
        if 'exemplar_analysis_log' in inspector.get_table_names():
            log_cols = {c['name'] for c in inspector.get_columns('exemplar_analysis_log')}
            if 'superseded_at' not in log_cols:
                try:
                    db.session.execute(text(
                        'ALTER TABLE exemplar_analysis_log '
                        'ADD COLUMN superseded_at TIMESTAMP'
                    ))
                    db.session.execute(text(
                        'CREATE INDEX IF NOT EXISTS '
                        'ix_exemplar_log_superseded ON exemplar_analysis_log (superseded_at)'
                    ))
                    db.session.commit()
                    logger.info('Added superseded_at column to exemplar_analysis_log')
                except Exception as _e:
                    db.session.rollback()
                    logger.warning(f'exemplar_analysis_log ALTER ADD superseded_at failed: {_e}')

        # Backfill exemplar_analysis_log from existing
        # Assignment.exemplar_analysis_json rows. One log entry per
        # already-analysed assignment, using exemplar_analyzed_at as
        # the historical created_at. Idempotent: only inserts rows
        # that don't already have a log entry.
        # submissions_count and roster_size stay NULL for backfilled
        # rows — they were never recorded; clustering can weight
        # NULL-sample rows lower.
        if (
            'exemplar_analysis_log' in inspector.get_table_names()
            and 'assignments' in inspector.get_table_names()
        ):
            try:
                db.session.execute(text(
                    "INSERT INTO exemplar_analysis_log "
                    "(assignment_id, areas_json, created_at) "
                    "SELECT a.id, a.exemplar_analysis_json, "
                    "       COALESCE(a.exemplar_analyzed_at, CURRENT_TIMESTAMP) "
                    "FROM assignments a "
                    "WHERE a.exemplar_analysis_json IS NOT NULL "
                    "  AND a.exemplar_analysis_json != '' "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM exemplar_analysis_log l "
                    "    WHERE l.assignment_id = a.id"
                    "  )"
                ))
                db.session.commit()
            except Exception as _e:
                db.session.rollback()
                logger.warning(f'exemplar_analysis_log backfill skipped: {_e}')

        if 'feedback_edit' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('feedback_edit')]
            if 'amend_answer_key' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN amend_answer_key BOOLEAN DEFAULT FALSE NOT NULL'))
                db.session.commit()
                logger.info('Added amend_answer_key column to feedback_edit table')
            # promoted_to_subject_standard_id, scope, promoted_by, promoted_at:
            # removed from ORM and dropped by _migrate_drop_subject_standards.
            # Do NOT re-add them here.

        if 'assignments' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('assignments')]
            # topic_keys and topic_keys_status removed from ORM and dropped by
            # _migrate_drop_subject_standards. Do NOT re-add them here.
            if 'bank_pushed_at' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN bank_pushed_at TIMESTAMP"))
                db.session.commit()
                logger.info('Added bank_pushed_at column to assignments table')


_DEPT_GOAL_MIGRATION_NAME = 'department_goal_dept_level_2026_05_15'


def _migrate_department_goal_to_dept_level(app):
    """One-shot wipe + reshape for DepartmentGoal.

    Goals used to be (target_band, target_subject); we're moving to
    (department_id, target_level, target_subject). Per Joe's call on a
    testing platform: drop every existing row rather than try to migrate
    stale text-band values, then ensure the new columns are present.
    Idempotent via MigrationFlag.
    """
    from sqlalchemy import text, inspect
    with app.app_context():
        marker = MigrationFlag.query.filter_by(name=_DEPT_GOAL_MIGRATION_NAME).first()
        if marker is not None:
            return
        inspector = inspect(db.engine)
        if 'department_goal' not in inspector.get_table_names():
            db.session.add(MigrationFlag(name=_DEPT_GOAL_MIGRATION_NAME))
            db.session.commit()
            return
        # Wipe every existing goal row. The user has explicitly opted in
        # to losing the rows rather than translating target_band strings.
        try:
            deleted = db.session.execute(text('DELETE FROM department_goal')).rowcount
        except Exception:
            db.session.rollback()
            deleted = 0
        else:
            db.session.commit()
        cols = {c['name'] for c in inspector.get_columns('department_goal')}
        if 'target_level' not in cols:
            db.session.execute(text(
                'ALTER TABLE department_goal ADD COLUMN target_level VARCHAR(20)'
            ))
            db.session.commit()
        if 'department_id' not in cols:
            # SQLite can't add a FK constraint via ALTER TABLE, but the
            # column itself is enough — the ORM enforces the relationship
            # on insert. On Postgres the FK gets added when create_all
            # rebuilds; manual ALTER avoided here for SQLite parity.
            db.session.execute(text(
                'ALTER TABLE department_goal ADD COLUMN department_id INTEGER'
            ))
            db.session.commit()
        if 'target_band' in cols:
            # Drop the legacy column where the dialect supports it.
            # SQLite < 3.35 lacks DROP COLUMN entirely — fall back to
            # leaving the column behind (harmless; never read).
            try:
                db.session.execute(text(
                    'ALTER TABLE department_goal DROP COLUMN target_band'
                ))
                db.session.commit()
                logger.info('Dropped legacy department_goal.target_band')
            except Exception:
                db.session.rollback()
                logger.info(
                    'Could not DROP COLUMN target_band on this dialect; '
                    'leaving it in place (unread).'
                )

        # Same rename pass for department_dashboard_layout: last_band →
        # last_level + add last_dept_id. We don't wipe layouts — they're
        # cheap to preserve and the column names already encode the new
        # dept-tab axis.
        if 'department_dashboard_layout' in inspector.get_table_names():
            layout_cols = {c['name'] for c in inspector.get_columns(
                'department_dashboard_layout')}
            if 'last_level' not in layout_cols:
                db.session.execute(text(
                    'ALTER TABLE department_dashboard_layout '
                    'ADD COLUMN last_level VARCHAR(20)'
                ))
                db.session.commit()
                if 'last_band' in layout_cols:
                    # Copy carries the old viewer's tab choice forward.
                    db.session.execute(text(
                        'UPDATE department_dashboard_layout '
                        'SET last_level = last_band '
                        'WHERE last_level IS NULL'
                    ))
                    db.session.commit()
            if 'last_dept_id' not in layout_cols:
                db.session.execute(text(
                    'ALTER TABLE department_dashboard_layout '
                    'ADD COLUMN last_dept_id INTEGER'
                ))
                db.session.commit()
            if 'last_band' in layout_cols:
                try:
                    db.session.execute(text(
                        'ALTER TABLE department_dashboard_layout '
                        'DROP COLUMN last_band'
                    ))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.info(
                        'Could not DROP COLUMN last_band on this dialect; '
                        'leaving it in place (unread).'
                    )

        db.session.add(MigrationFlag(name=_DEPT_GOAL_MIGRATION_NAME))
        db.session.commit()
        logger.info(
            'department_goal migration: wiped %d existing rows; '
            'added target_level + department_id columns; '
            'renamed dashboard_layout.last_band → last_level + last_dept_id',
            deleted,
        )


def _drop_columns(table_name, columns_to_drop):
    """Drop columns from a table on both SQLite and Postgres.

    SQLite (pre-3.35) had no DROP COLUMN, so we use the table-rebuild
    dance. Portable, and lets us drop multiple columns in one pass.

    Postgres supports native ALTER TABLE ... DROP COLUMN.

    Idempotent: silently ignores columns that don't exist.
    Uses db.session.execute(text(...)) for SQLAlchemy 2.0 compatibility.
    """
    from sqlalchemy import text, inspect as sa_inspect
    dialect = db.engine.dialect.name

    if dialect == 'postgresql':
        inspector = sa_inspect(db.engine)
        existing = {c['name'] for c in inspector.get_columns(table_name)}
        for col in columns_to_drop:
            if col not in existing:
                continue
            db.session.execute(text(f'ALTER TABLE {table_name} DROP COLUMN {col}'))
            db.session.commit()
        db.session.commit()
        return

    # SQLite path: table rebuild.
    inspector = sa_inspect(db.engine)
    try:
        cols_info = inspector.get_columns(table_name)
    except Exception:
        return  # table doesn't exist
    if not cols_info:
        return
    existing_cols = [c['name'] for c in cols_info]
    to_drop = set(c for c in columns_to_drop if c in existing_cols)
    if not to_drop:
        return  # idempotent no-op

    # We need raw PRAGMA info for type/notnull/default/pk details.
    info = db.session.execute(text(f'PRAGMA table_info({table_name})')).fetchall()

    kept_cols = [c for c in existing_cols if c not in to_drop]
    kept_cols_csv = ', '.join(kept_cols)

    new_table = f'__new__{table_name}'
    db.session.execute(text(f'DROP TABLE IF EXISTS {new_table}'))

    col_defs = []
    pk_cols = []
    for r in info:
        cid, name, ctype, notnull, dflt, pk = r
        if name in to_drop:
            continue
        line = f'{name} {ctype}'
        if notnull:
            line += ' NOT NULL'
        if dflt is not None:
            line += f' DEFAULT {dflt}'
        if pk:
            pk_cols.append(name)
        col_defs.append(line)
    if pk_cols:
        col_defs.append(f'PRIMARY KEY ({", ".join(pk_cols)})')

    db.session.execute(text(
        f'CREATE TABLE {new_table} ({", ".join(col_defs)})'
    ))
    db.session.execute(text(
        f'INSERT INTO {new_table} ({kept_cols_csv}) '
        f'SELECT {kept_cols_csv} FROM {table_name}'
    ))
    db.session.execute(text(f'DROP TABLE {table_name}'))
    db.session.execute(text(f'ALTER TABLE {new_table} RENAME TO {table_name}'))

    # Recreate indexes that existed on the old table.
    indexes = db.session.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:t "
        "AND sql IS NOT NULL"
    ), {'t': table_name}).fetchall()
    for (sql,) in indexes:
        try:
            db.session.execute(text(sql))
        except Exception:
            pass  # index already created during table rebuild
    db.session.commit()


_DROP_SUBJECT_STANDARDS_MIGRATION_NAME = 'drop_subject_standards_2026_05_16'


def _migrate_drop_subject_standards(_app, force=False):
    """Drop subject_standards + subject_topic_vocabulary tables and the
    obsolete FeedbackEdit + Assignment columns. Idempotent via
    MigrationFlag. force=True bypasses idempotency (tests only).

    Uses raw SQL throughout: by this commit, the ORM no longer maps the
    affected columns, so SQLAlchemy queries would fail at parse time.
    """
    with _app.app_context():
        marker = MigrationFlag.query.filter_by(
            name=_DROP_SUBJECT_STANDARDS_MIGRATION_NAME
        ).first()
        if marker is not None and not force:
            logger.debug(
                'drop_subject_standards migration already applied at %s',
                marker.applied_at,
            )
            return
        if force:
            logger.info('drop_subject_standards: forced re-run (tests only)')
        else:
            logger.info('drop_subject_standards: first run on this DB')

        from sqlalchemy import text as _text

        # 1. Deactivate legacy promoted-only FeedbackEdits.
        try:
            db.session.execute(_text(
                "UPDATE feedback_edit "
                "SET active = 0 "
                "WHERE scope = 'promoted' AND amend_answer_key = 0 AND active = 1"
            ))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f'legacy-promoted deactivation skipped: {e}')

        # 2. Drop tables.
        try:
            db.session.execute(_text('DROP TABLE IF EXISTS subject_standards'))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f'DROP TABLE subject_standards skipped: {e}')
        try:
            db.session.execute(_text('DROP TABLE IF EXISTS subject_topic_vocabulary'))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f'DROP TABLE subject_topic_vocabulary skipped: {e}')

        # 3. Drop columns.
        _drop_columns('feedback_edit', [
            'scope', 'promoted_to_subject_standard_id',
            'promoted_by', 'promoted_at',
        ])
        _drop_columns('assignments', ['topic_keys', 'topic_keys_status'])

        if marker is None:
            db.session.add(MigrationFlag(name=_DROP_SUBJECT_STANDARDS_MIGRATION_NAME))
            db.session.commit()


_CALIBRATION_RUNTIME_MIGRATION_NAME = 'calibration_runtime_2026_05_13'


def _migrate_calibration_runtime(_app, force=False):
    """Backfills amend_answer_key=1 on every active FeedbackEdit except
    promoted-only rows (scope='promoted', amend_answer_key=0), which are
    left for _migrate_drop_subject_standards to deactivate. Also deactivates
    orphan FeedbackEdits whose parent assignment no longer exists, and marks
    all MarkingPrinciplesCache rows stale. Uses a column-existence check so
    it works on both pre-drop and post-drop schemas.

    Idempotent via MigrationFlag. force=True bypasses idempotency (tests only).
    """
    with _app.app_context():
        marker = MigrationFlag.query.filter_by(name=_CALIBRATION_RUNTIME_MIGRATION_NAME).first()
        if marker is not None and not force:
            logger.debug(
                'Calibration runtime migration already applied at %s — skipping',
                marker.applied_at,
            )
            return
        if force:
            logger.info('Calibration runtime migration: forced re-run (tests only)')
        else:
            logger.info(
                'Calibration runtime migration: first run on this DB — '
                'classifying assignments by 5-day cutoff'
            )

        # FeedbackEdit calibration intent backfill: flip amend_answer_key=True
        # on every still-active row, EXCEPT promoted-only rows (those get
        # deactivated by _migrate_drop_subject_standards below). Uses raw SQL
        # because the `scope` column is no longer mapped by the ORM after
        # commit 4, but is still present in the schema at this point in the
        # boot sequence (the column drop happens in the next migration).
        from sqlalchemy import text as _text_cal, inspect as _inspect_cal
        _fe_cols = {c['name'] for c in _inspect_cal(db.engine).get_columns('feedback_edit')}
        _scope_exists = 'scope' in _fe_cols
        try:
            if _scope_exists:
                db.session.execute(_text_cal(
                    "UPDATE feedback_edit "
                    "SET amend_answer_key = 1 "
                    "WHERE active = 1 "
                    "AND NOT (scope = 'promoted' AND amend_answer_key = 0)"
                ))
            else:
                # scope column already dropped (e.g. after _migrate_drop_subject_standards
                # ran on this DB); safe to set all active rows as amendments.
                db.session.execute(_text_cal(
                    "UPDATE feedback_edit SET amend_answer_key = 1 WHERE active = 1"
                ))
        except Exception as e:
            logger.warning(f'amend_answer_key backfill skipped: {e}')

        # Orphan-assignment cleanup: deactivate edits whose parent
        # assignment is gone.
        try:
            db.session.execute(_text_cal(
                "UPDATE feedback_edit "
                "SET active = 0 "
                "WHERE active = 1 "
                "AND assignment_id NOT IN (SELECT id FROM assignments)"
            ))
        except Exception as e:
            logger.warning(f'orphan FeedbackEdit deactivation skipped: {e}')

        # Deactivate MarkingPrinciplesCache — mark all stale so the old
        # principles file stops being applied.
        db.session.query(MarkingPrinciplesCache).update({'is_stale': True})

        db.session.commit()

        if marker is None:
            db.session.add(MigrationFlag(name=_CALIBRATION_RUNTIME_MIGRATION_NAME))
            db.session.commit()


_THEME_KEY_TO_MISTAKE_TYPE_MIGRATION_NAME = 'rename_theme_key_to_mistake_type_2026_05_16'


def _migrate_result_json_theme_to_mistake_type(_app, force=False):
    """Rewrite legacy 'theme_key' JSON keys to 'mistake_type' across every
    Submission.result_json blob. One-shot, idempotent via MigrationFlag.

    Touches:
      questions[].theme_key            → mistake_type
      questions[].theme_key_corrected  → mistake_type_corrected
      _tiered.reviewed_theme_keys      → reviewed_mistake_types
      _tiered.group_habits[].theme_key → mistake_type
      _tiered.corrections[].theme_key  → mistake_type

    Per-submission errors are swallowed and logged — a single bad row
    doesn't block the rest of the backfill, and the boot path keeps
    going either way.
    """
    with _app.app_context():
        marker = MigrationFlag.query.filter_by(
            name=_THEME_KEY_TO_MISTAKE_TYPE_MIGRATION_NAME,
        ).first()
        if marker is not None and not force:
            logger.debug(
                'result_json theme_key → mistake_type migration already applied at %s — skipping',
                marker.applied_at,
            )
            return
        logger.info(
            'result_json theme_key → mistake_type migration: rewriting JSON keys',
        )

        rewritten = 0
        skipped_errors = 0
        for sub in Submission.query.all():
            try:
                raw = sub.result_json
                if not raw:
                    continue
                # Cheap pre-check: if no occurrence of the legacy key,
                # skip the JSON load entirely.
                if 'theme_key' not in raw and 'reviewed_theme_keys' not in raw:
                    continue
                data = sub.get_result() or {}
                changed = False
                for q in (data.get('questions') or []):
                    if not isinstance(q, dict):
                        continue
                    if 'theme_key' in q:
                        q['mistake_type'] = q.pop('theme_key')
                        changed = True
                    if 'theme_key_corrected' in q:
                        q['mistake_type_corrected'] = q.pop('theme_key_corrected')
                        changed = True
                tiered = data.get('_tiered')
                if isinstance(tiered, dict):
                    if 'reviewed_theme_keys' in tiered:
                        tiered['reviewed_mistake_types'] = tiered.pop('reviewed_theme_keys')
                        changed = True
                    for h in (tiered.get('group_habits') or []):
                        if isinstance(h, dict) and 'theme_key' in h:
                            h['mistake_type'] = h.pop('theme_key')
                            changed = True
                    for c in (tiered.get('corrections') or []):
                        if isinstance(c, dict) and 'theme_key' in c:
                            c['mistake_type'] = c.pop('theme_key')
                            changed = True
                if changed:
                    sub.set_result(data)
                    rewritten += 1
            except Exception:
                skipped_errors += 1
                logger.exception(
                    'result_json theme_key migration: failed for submission %s', sub.id,
                )

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception('result_json theme_key migration: commit failed')

        logger.info(
            'result_json theme_key → mistake_type migration: rewrote %d row(s), %d error(s)',
            rewritten, skipped_errors,
        )

        if marker is None:
            db.session.add(MigrationFlag(name=_THEME_KEY_TO_MISTAKE_TYPE_MIGRATION_NAME))
            db.session.commit()


def _sweep_stuck_submissions(app):
    """UP-06: flip submissions stuck in an in-flight status older than 10
    minutes to 'error'. The job system (`jobs = {}`) is in-memory, so a
    Railway redeploy mid-bulk-mark leaves rows pending forever — invisible
    to the bulk loop's exception handler because the worker process died.

    Idempotent: filtered by status + cutoff, safe to run on every boot.
    Best-effort: a failure here never blocks startup.
    """
    with app.app_context():
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=10)
            stuck_statuses = ('pending', 'processing', 'extracting', 'preview')
            stuck = Submission.query.filter(
                Submission.status.in_(stuck_statuses),
                Submission.submitted_at < cutoff,
            ).all()
            if not stuck:
                return
            for s in stuck:
                s.status = 'error'
                s.set_result({'error': 'Marking worker died during deploy — please retry.'})
                if not s.marked_at:
                    s.marked_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(f'UP-06 sweep: marked {len(stuck)} stuck submission(s) as error')
        except Exception as e:
            db.session.rollback()
            logger.warning(f'UP-06 stuck-submission sweep failed: {e}')


def _sweep_stuck_bulk_jobs(app):
    """UP-15: flip BulkJob rows stuck in 'processing' more than 30 minutes
    to 'error'. Same shape as `_sweep_stuck_submissions` — a redeploy
    mid-bulk-mark would otherwise leave a row reading 'processing' forever
    even though the worker is gone.

    Idempotent, best-effort, never blocks startup.
    """
    with app.app_context():
        try:
            from sqlalchemy import inspect as _i
            if 'bulk_jobs' not in _i(db.engine).get_table_names():
                return
            # `started_at` is tz-aware; comparing against naive `utcnow()` would
            # raise on PostgreSQL and silently mis-compare on SQLite.
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
            stuck = BulkJob.query.filter(
                BulkJob.status == 'processing',
                BulkJob.started_at < cutoff,
            ).all()
            if not stuck:
                return
            for j in stuck:
                j.status = 'error'
                j.error_message = (j.error_message or '') + ' Worker died during deploy — please re-trigger.'
                j.finished_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(f'UP-15 sweep: marked {len(stuck)} stuck bulk job(s) as error')
        except Exception as e:
            db.session.rollback()
            logger.warning(f'UP-15 bulk-job sweep failed: {e}')


def init_db(app):
    """Configure and initialize database."""
    db_url = os.getenv('DATABASE_URL', '')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    if not db_url:
        db_url = 'sqlite:///marking.db'
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with app.app_context():
        # Concurrent gunicorn workers each call init_db() at boot. On
        # PostgreSQL two workers racing `db.create_all()` can both pass
        # the internal "does this table exist?" check, both issue
        # `CREATE TABLE bulk_jobs ...`, and one then dies with
        # `pg_type_typname_nsp_index UniqueViolation` because the table
        # already exists. Serialise with a session-level advisory lock so
        # only one worker runs schema bootstrap at a time; the other
        # blocks here, then wakes up and runs an idempotent no-op
        # create_all + idempotent migrations.
        boot_conn = None
        boot_lock_held = False
        if db.engine.dialect.name == 'postgresql':
            try:
                boot_conn = db.engine.connect()
                boot_conn.exec_driver_sql('SELECT pg_advisory_lock(8989898989)')
                boot_lock_held = True
            except Exception:
                logger.warning(
                    'Failed to acquire boot advisory lock — proceeding '
                    'without serialisation (concurrent create_all may race)',
                    exc_info=True,
                )
                if boot_conn is not None:
                    try:
                        boot_conn.close()
                    except Exception:
                        pass
                    boot_conn = None
        try:
            db.create_all()
            _migrate_add_columns(app)
            _migrate_department_goal_to_dept_level(app)
            _migrate_calibration_runtime(app)
            _migrate_result_json_theme_to_mistake_type(app)
            _migrate_drop_subject_standards(app)
            if os.getenv('DEPT_MODE', 'FALSE').upper() == 'TRUE':
                seed_departments()
                backfill_teacher_departments()
                sync_dept_subjects()
            _sweep_stuck_submissions(app)
            _sweep_stuck_bulk_jobs(app)
        finally:
            if boot_lock_held and boot_conn is not None:
                try:
                    boot_conn.exec_driver_sql('SELECT pg_advisory_unlock(8989898989)')
                except Exception:
                    logger.exception('failed to release boot advisory lock')
                try:
                    boot_conn.close()
                except Exception:
                    pass
        # Belt-and-suspenders: confirm feedback_edit table actually exists.
        # If create_all silently failed for any reason, force-create it now
        # so calibration writes don't go to /dev/null.
        from sqlalchemy import inspect as _inspect
        existing = set(_inspect(db.engine).get_table_names())
        if 'feedback_edit' not in existing:
            logger.error('feedback_edit table missing after create_all — forcing creation')
            try:
                FeedbackEdit.__table__.create(db.engine)
                logger.error('feedback_edit table created via fallback')
            except Exception:
                logger.exception('fallback feedback_edit create failed')
        else:
            logger.info('feedback_edit table present at boot')


class Teacher(db.Model):
    __tablename__ = 'teachers'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    role = db.Column(db.String(20), default='teacher')  # hod, subject_head, lead, manager, teacher
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    classes = db.relationship('Class', secondary='teacher_classes', back_populates='teachers')


class Class(db.Model):
    __tablename__ = 'classes'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(100), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    teachers = db.relationship('Teacher', secondary='teacher_classes', back_populates='classes')
    assignments = db.relationship('Assignment', backref='dept_class', lazy=True)
    students = db.relationship('Student', backref='student_class', lazy=True, cascade='all, delete-orphan')


class TeacherClass(db.Model):
    __tablename__ = 'teacher_classes'
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'), primary_key=True)
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), primary_key=True)


class DepartmentConfig(db.Model):
    __tablename__ = 'department_config'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, default='')


class Department(db.Model):
    """A school department, owning a set of subjects and a set of teachers.

    Hard scopes HOD permissions: a teacher with role='hod' and
    is_lead=True in TeacherDepartment for dept X may only act on
    teachers / assignments / classes scoped to dept X.

    Spec: docs/superpowers/specs/2026-05-14-teacher-department-tags-design.md
    """
    __tablename__ = 'departments'
    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    short_name  = db.Column(db.String(24), nullable=False, default='')
    sort_order  = db.Column(db.Integer, default=0, nullable=False)
    is_active   = db.Column(db.Boolean, default=True, nullable=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class DepartmentSubject(db.Model):
    """M2M between Department and subjects.py:SUBJECT_KEYS.

    `subject_key` is a string FK-by-convention to subjects.py (we don't
    have a subjects table). A unit test enforces every row matches a
    current canonical key.
    """
    __tablename__ = 'department_subjects'
    department_id = db.Column(db.Integer,
                              db.ForeignKey('departments.id', ondelete='CASCADE'),
                              primary_key=True)
    subject_key   = db.Column(db.String(80), primary_key=True)


class TeacherDepartment(db.Model):
    """M2M between Teacher and Department.

    `is_lead=True` makes the teacher HOD of the dept. A teacher may
    lead multiple depts; a dept may have multiple lead teachers.
    """
    __tablename__ = 'teacher_departments'
    teacher_id    = db.Column(db.String(36),
                              db.ForeignKey('teachers.id', ondelete='CASCADE'),
                              primary_key=True)
    department_id = db.Column(db.Integer,
                              db.ForeignKey('departments.id', ondelete='CASCADE'),
                              primary_key=True)
    is_lead       = db.Column(db.Boolean, default=False, nullable=False)
    added_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Department seed + backfill (2026-05-14 multi-dept design)
# ---------------------------------------------------------------------------

_DEFAULT_DEPARTMENTS = [
    # (sort_order, name, short_name, subject_keys)
    (10, 'Aesthetics and Craft and Technology', 'Aesthetics',
     ['art', 'music', 'design_and_technology', 'nutrition_and_food_science']),
    (20, 'English Language and Literature', 'English',
     ['english', 'literature_in_english']),
    (30, 'Humanities', 'Humanities',
     ['geography', 'social_studies', 'history']),
    (40, 'Mathematics and Principles of Accounts', 'Maths',
     ['mathematics', 'principles_of_accounts']),
    (50, 'Mother Tongue Language', 'MTL',
     ['chinese', 'malay', 'tamil']),
    (60, 'Science', 'Science',
     ['chemistry', 'biology', 'computing', 'computer_applications',
      'physics', 'lower_secondary_science', 'science']),
]


# Bump this when you add subjects to _DEFAULT_DEPARTMENTS for an
# existing deployment. The sync migration is named after the version
# so each addition gets a fresh idempotency flag.
_DEPT_SUBJECTS_SYNC_MIGRATION_NAME = 'dept_subjects_sync_v1'


def sync_dept_subjects():
    """Ensure every (department, subject_key) pair in
    _DEFAULT_DEPARTMENTS exists in `department_subjects`.

    `seed_departments` only runs on an empty Department table, so when
    we add a new subject to an existing deployment its mapping never
    lands. This migration walks the canonical list and inserts any
    missing rows. Idempotent via MigrationFlag — bump the flag name
    when you add another subject.
    """
    marker = MigrationFlag.query.filter_by(
        name=_DEPT_SUBJECTS_SYNC_MIGRATION_NAME).first()
    if marker is not None:
        return
    if Department.query.first() is None:
        # Fresh DB — seed_departments will handle it. Still mark done so
        # we don't keep checking on every boot.
        db.session.add(MigrationFlag(name=_DEPT_SUBJECTS_SYNC_MIGRATION_NAME))
        db.session.commit()
        return
    inserted = 0
    for _sort, name, _short, subject_keys in _DEFAULT_DEPARTMENTS:
        dept = Department.query.filter_by(name=name).first()
        if dept is None:
            continue
        existing = {ds.subject_key for ds in DepartmentSubject.query
                    .filter_by(department_id=dept.id).all()}
        for sk in subject_keys:
            if sk in existing:
                continue
            db.session.add(DepartmentSubject(department_id=dept.id, subject_key=sk))
            inserted += 1
    db.session.add(MigrationFlag(name=_DEPT_SUBJECTS_SYNC_MIGRATION_NAME))
    db.session.commit()
    if inserted:
        logger.info('sync_dept_subjects: inserted %d missing DepartmentSubject rows', inserted)


def seed_departments():
    """Seed the 6 default depts + subject mapping. Idempotent."""
    if Department.query.first() is not None:
        return
    for sort_order, name, short_name, subject_keys in _DEFAULT_DEPARTMENTS:
        dept = Department(name=name, short_name=short_name, sort_order=sort_order)
        db.session.add(dept)
        db.session.flush()
        for sk in subject_keys:
            db.session.add(DepartmentSubject(department_id=dept.id, subject_key=sk))
    db.session.commit()
    logger.info('Seeded %d departments', len(_DEFAULT_DEPARTMENTS))


_TEACHER_DEPT_BACKFILL_MIGRATION_NAME = 'teacher_dept_tags_backfill_v1'


def backfill_teacher_departments(force=False):
    """One-shot backfill of TeacherDepartment from assignment history.

    For each teacher: pull DISTINCT assignment subjects, resolve to
    canonical keys, look up the owning Department, insert membership.
    Promote role='hod' teachers to is_lead on every dept they touched.

    Idempotent via MigrationFlag. force=True bypasses (tests only).
    """
    from subjects import resolve_subject_key

    if not force:
        marker = MigrationFlag.query.filter_by(
            name=_TEACHER_DEPT_BACKFILL_MIGRATION_NAME).first()
        if marker is not None:
            return

    subject_to_dept_id = {ds.subject_key: ds.department_id
                          for ds in DepartmentSubject.query.all()}
    if not subject_to_dept_id:
        logger.warning('backfill_teacher_departments: no DepartmentSubject '
                       'rows; seed_departments must run first')
        return

    inserted = 0
    promoted = 0
    for teacher in Teacher.query.all():
        subjects = {a.subject for a in Assignment.query
                    .filter_by(teacher_id=teacher.id).all() if a.subject}
        dept_ids = set()
        for subj in subjects:
            key = resolve_subject_key(subj)
            if key is None:
                continue
            did = subject_to_dept_id.get(key)
            if did is not None:
                dept_ids.add(did)
        for did in dept_ids:
            existing = TeacherDepartment.query.filter_by(
                teacher_id=teacher.id, department_id=did).first()
            if existing is not None:
                continue
            is_lead = (teacher.role == 'hod')
            db.session.add(TeacherDepartment(
                teacher_id=teacher.id, department_id=did, is_lead=is_lead))
            inserted += 1
            if is_lead:
                promoted += 1

    if not force:
        db.session.add(MigrationFlag(name=_TEACHER_DEPT_BACKFILL_MIGRATION_NAME))
    db.session.commit()
    logger.info('backfill_teacher_departments: inserted %d rows (%d as lead)',
                inserted, promoted)


class Assignment(db.Model):
    __tablename__ = 'assignments'

    id = db.Column(db.String(36), primary_key=True)
    classroom_code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    title = db.Column(db.String(300), default='')
    subject = db.Column(db.String(200), default='')
    assign_type = db.Column(db.String(20), default='short_answer')
    scoring_mode = db.Column(db.String(20), default='marks')
    total_marks = db.Column(db.String(20), default='')
    provider = db.Column(db.String(20), default='anthropic')
    model = db.Column(db.String(100), default='')
    show_results = db.Column(db.Boolean, default=True)
    allow_drafts = db.Column(db.Boolean, default=False)
    max_drafts = db.Column(db.Integer, default=3)
    review_instructions = db.Column(db.Text, default='')
    marking_instructions = db.Column(db.Text, default='')

    # Department mode foreign keys
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), nullable=True, index=True)
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=True, index=True)

    # File storage as binary
    question_paper = db.Column(db.LargeBinary)
    answer_key = db.Column(db.LargeBinary)
    rubrics = db.Column(db.LargeBinary)
    reference = db.Column(db.LargeBinary)

    # API keys (JSON string, encrypted with Fernet when FLASK_SECRET_KEY is set)
    api_keys_json = db.Column(db.Text, default='{}')

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_edited_at = db.Column(db.DateTime, nullable=True)
    needs_remark = db.Column(db.Boolean, default=False, nullable=False)
    exemplar_analysis_json = db.Column(db.Text)
    exemplar_analyzed_at = db.Column(db.DateTime)
    # Hanyu pinyin annotation mode for Chinese-subject feedback. Only takes
    # effect when subject resolves to the chinese family. Values:
    #   'off'   — no pinyin (default; matches pre-feature behaviour)
    #   'vocab' — annotate HSK 4+ words only
    #   'full'  — annotate every CJK character
    pinyin_mode = db.Column(db.String(10), default='off', nullable=False)
    # New (calibration intent design 2026-05-13)
    bank_pushed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    students = db.relationship('Student', backref='assignment', lazy=True, cascade='all, delete-orphan')

    def get_api_keys(self):
        raw = self.api_keys_json or '{}'
        f = _get_fernet()
        if f:
            try:
                decrypted = f.decrypt(raw.encode()).decode()
                return json.loads(decrypted)
            except (InvalidToken, Exception):
                pass  # Fall through to plaintext (pre-encryption data)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_api_keys(self, keys_dict):
        plaintext = json.dumps(keys_dict)
        f = _get_fernet()
        if f:
            self.api_keys_json = f.encrypt(plaintext.encode()).decode()
        else:
            self.api_keys_json = plaintext


class AssignmentBank(db.Model):
    __tablename__ = 'assignment_bank'

    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(300), default='')
    subject = db.Column(db.String(200), default='')
    level = db.Column(db.String(20), default='')  # Sec 1, Sec 2, ... Sec 5
    tags = db.Column(db.Text, default='')  # comma-separated hashtags
    assign_type = db.Column(db.String(20), default='short_answer')
    scoring_mode = db.Column(db.String(20), default='marks')
    total_marks = db.Column(db.String(20), default='')
    review_instructions = db.Column(db.Text, default='')
    marking_instructions = db.Column(db.Text, default='')

    # Default settings copied into class assignments by bank_use(). Mirrors
    # the equivalent fields on Assignment so a bank item can carry per-class
    # defaults beyond just text + PDFs.
    provider = db.Column(db.String(50), default='')
    model = db.Column(db.String(100), default='')
    pinyin_mode = db.Column(db.String(10), default='off')
    show_results = db.Column(db.Boolean, default=True)
    allow_drafts = db.Column(db.Boolean, default=False)
    max_drafts = db.Column(db.Integer, default=3)

    question_paper = db.Column(db.LargeBinary)
    answer_key = db.Column(db.LargeBinary)
    rubrics = db.Column(db.LargeBinary)
    reference = db.Column(db.LargeBinary)

    # Text-only "Teacher clarifications" appended to the answer key. Stored
    # separately from `answer_key` so a PDF answer key is preserved as-is and
    # bank_preview.html can offer a right-pane dropdown to switch between the
    # PDF and these textual amendments.
    answer_key_amendments = db.Column(db.Text, default='')

    created_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship('Teacher', backref='bank_items', lazy=True)

    def get_tags_list(self):
        if not self.tags:
            return []
        return [t.strip().lstrip('#') for t in self.tags.split(',') if t.strip()]

    def set_tags_list(self, tags):
        self.tags = ','.join('#' + t.strip().lstrip('#') for t in tags if t.strip())


class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), nullable=True, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=True, index=True)
    index_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    submissions = db.relationship('Submission', backref='student', lazy=True, cascade='all, delete-orphan')


class Submission(db.Model):
    __tablename__ = 'submissions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False, index=True)
    script_bytes = db.Column(db.LargeBinary)
    script_pages_json = db.Column(db.Text)  # JSON list of base64-encoded file bytes
    status = db.Column(db.String(20), default='pending')  # pending, extracting, preview, processing, done, error
    result_json = db.Column(db.Text)
    extracted_text_json = db.Column(db.Text)  # AI-extracted answers (original)
    student_text_json = db.Column(db.Text)  # Student-confirmed answers (may be edited)
    student_amended = db.Column(db.Boolean, default=False)  # True if student edited extracted text
    draft_number = db.Column(db.Integer, default=1, nullable=False)
    is_final = db.Column(db.Boolean, default=True, nullable=False, index=True)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    marked_at = db.Column(db.DateTime)
    feedback_opened_at = db.Column(db.DateTime)  # first time the student opened the tiered feedback page
    correction_submitted_at = db.Column(db.DateTime)  # first time the student submitted a "Now You Try" correction
    # Async "Group by Mistake Type" categorisation — pending on kick-off,
    # done once the background thread writes categorisation + group_habits
    # into result_json, failed if the AI call errored.
    categorisation_status = db.Column(db.String(20), default='pending')
    # UP-12: per-call AI usage log (tokens + latency + cost + cache stats).
    # Optional — legacy rows have NULL, readers must tolerate that.
    usage_json = db.Column(db.Text)

    assignment = db.relationship('Assignment', backref=db.backref('submissions', cascade='all, delete-orphan'))

    def get_script_pages(self):
        """Return list of file bytes for all uploaded pages."""
        if self.script_pages_json:
            pages = json.loads(self.script_pages_json)
            return [base64.b64decode(p) for p in pages]
        if self.script_bytes:
            return [self.script_bytes]
        return []

    def set_script_pages(self, pages_list):
        """Store list of file bytes as base64 JSON."""
        self.script_pages_json = json.dumps([base64.b64encode(p).decode() for p in pages_list], ensure_ascii=False)

    def get_result(self) -> dict[str, Any]:
        # UP-36: declares the persisted marking-result shape so callers
        # know to expect either the success dict ({'questions': [...],
        # 'overall_feedback': str, ...}) or the failure shape
        # ({'error': str}) written by the orchestrators after UP-35.
        try:
            return json.loads(self.result_json or '{}')
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_result(self, result_dict):
        self.result_json = json.dumps(result_dict, ensure_ascii=False)

    def get_extracted_text(self):
        try:
            return json.loads(self.extracted_text_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    def set_extracted_text(self, answers_list):
        self.extracted_text_json = json.dumps(answers_list, ensure_ascii=False)

    def get_student_text(self):
        try:
            return json.loads(self.student_text_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    def set_student_text(self, answers_list):
        self.student_text_json = json.dumps(answers_list, ensure_ascii=False)

    def get_usage(self):
        """UP-12: per-call AI usage entries. Returns [] for legacy submissions
        with no `usage_json` column populated."""
        try:
            return json.loads(self.usage_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    def append_usage(self, entry):
        """Append a single AI-call usage entry. `entry` is a dict like
        {'provider', 'model', 'input_tokens', 'output_tokens',
         'cache_read_input_tokens', 'cache_creation_input_tokens',
         'latency_ms', 'cost_usd', 'ts'}.
        Readers tolerate missing keys per the schema-evolution policy."""
        items = self.get_usage()
        items.append(entry)
        self.usage_json = json.dumps(items, ensure_ascii=False)

    @classmethod
    def query_no_blobs(cls):
        """UP-10: return a `Submission.query` with the four large columns
        deferred. Use on analytics / insight / list-style queries that don't
        need the actual PDF bytes or extracted text — a 40-student class has
        ~100-300 MB of blobs, and loading them on every insight render is
        the single biggest perf liability in the codebase.

        Touching any deferred attribute later triggers a per-row lazy fetch;
        that's safe but defeats the perf win, so prefer to keep the blob
        attrs untouched in callers that use this helper.
        """
        from sqlalchemy.orm import defer
        return cls.query.options(
            defer(cls.script_bytes),
            defer(cls.script_pages_json),
            defer(cls.extracted_text_json),
            defer(cls.student_text_json),
        )


class FeedbackLog(db.Model):
    """Versioned audit log of teacher feedback edits. v1 = AI original,
    v2+ = teacher edits. Used for the edit-history view and the
    calibration anchor lookup so the bank row points back to the
    original AI text it was calibrating against."""
    __tablename__ = 'feedback_log'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('submissions.id'), nullable=False, index=True)
    criterion_id = db.Column(db.String(64), nullable=False)
    field = db.Column(db.String(20), nullable=False)  # 'feedback' | 'improvement'
    version = db.Column(db.Integer, nullable=False)
    feedback_text = db.Column(db.Text, nullable=False, default='')
    author_type = db.Column(db.String(10), nullable=False)  # 'ai' | 'teacher'
    author_id = db.Column(db.String(36), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('submission_id', 'criterion_id', 'field', 'version',
                            name='uq_feedback_log_sub_crit_field_ver'),
    )


class FeedbackEdit(db.Model):
    """Calibration bank — one row per teacher edit saved with the
    "Save to calibration bank" checkbox. Drives the calibration block
    prepended to future marking prompts and the propagation candidate
    detection that fires after each save.

    Subject grouping is via JOIN on assignments.subject (case-insensitive)
    against the canonical-dropdown string. mistake_type keeps its WITHIN-
    subject "nature of the mistake" categorisation role
    (config/mistake_themes.py is the single source of truth for keys).
    """
    __tablename__ = 'feedback_edit'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('submissions.id'), nullable=False, index=True)
    criterion_id = db.Column(db.String(64), nullable=False)
    field = db.Column(db.String(20), nullable=False)
    original_text = db.Column(db.Text, nullable=False, default='')
    edited_text = db.Column(db.Text, nullable=False, default='')
    edited_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=False, index=True)
    mistake_type = db.Column(db.String(64), nullable=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False, index=True)
    rubric_version = db.Column(db.String(64), nullable=False, default='')
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    propagation_status = db.Column(db.String(20), nullable=False, default='none')
    propagated_to = db.Column(db.Text, nullable=False, default='[]')
    propagated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    mistake_pattern = db.Column(db.String(80), nullable=True)
    correction_principle = db.Column(db.String(300), nullable=True)
    transferability = db.Column(db.String(10), nullable=True)
    # New (calibration intent design 2026-05-13)
    amend_answer_key = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (
        db.Index('ix_feedback_edit_lookup', 'edited_by', 'active', 'mistake_type'),
        db.Index('ix_feedback_edit_assignment', 'assignment_id', 'rubric_version'),
    )


class MigrationFlag(db.Model):
    """One-shot migration marker. Each named migration writes a single row
    here on completion to prevent re-running."""
    __tablename__ = 'migration_flag'
    name = db.Column(db.String(80), primary_key=True)
    applied_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc),
                           nullable=False)


class MarkingPrinciplesCache(db.Model):
    __tablename__ = 'marking_principles_cache'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # Canonical Assignment.subject string (e.g. 'Physics', 'Higher Chinese').
    # Case-insensitive matching is in the SQL — values are stored as-typed.
    subject = db.Column(db.String(80), nullable=False, unique=True)
    markdown_text = db.Column(db.Text, nullable=False, default='')
    generated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_stale = db.Column(db.Boolean, nullable=False, default=False)
    edit_count_at_gen = db.Column(db.Integer, nullable=False, default=0)
    has_conflicts = db.Column(db.Boolean, nullable=False, default=False)


class CategorisationCorrection(db.Model):
    __tablename__ = 'categorisation_correction'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('submissions.id'), nullable=False, index=True)
    criterion_id = db.Column(db.String(64), nullable=False)
    field = db.Column(db.String(20), nullable=False, default='mistake_type')
    original_mistake_type = db.Column(db.String(64), nullable=True)
    original_specific_label = db.Column(db.String(80), nullable=True)
    corrected_mistake_type = db.Column(db.String(64), nullable=False)
    corrected_specific_label = db.Column(db.String(80), nullable=True)
    corrected_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=False, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.Index('ix_cat_corr_assignment', 'assignment_id'),
    )


class BulkJob(db.Model):
    """UP-15: persistent state for bulk-mark and print-all jobs. Replaces
    the in-memory `jobs` dict in app.py for these long-running flows so a
    Railway redeploy mid-bulk-mark doesn't lose progress. The job_id is the
    same UUID the route hands the frontend, used as the primary key so the
    `/status/<job_id>` poller can `BulkJob.query.get(job_id)` directly.

    JSON columns store opaque dict payloads — readers must tolerate older
    shapes (per the schema-evolution policy)."""
    __tablename__ = 'bulk_jobs'

    id = db.Column(db.String(36), primary_key=True)
    kind = db.Column(db.String(20), nullable=False, default='bulk_mark')
    status = db.Column(db.String(20), nullable=False, default='processing')
    assignment_id = db.Column(db.String(36), nullable=True, index=True)
    subject = db.Column(db.String(120), nullable=True)
    progress_json = db.Column(db.Text)
    results_json = db.Column(db.Text)
    skipped_json = db.Column(db.Text)
    errors_json = db.Column(db.Text)
    error_message = db.Column(db.Text)
    started_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def _decode(self, col):
        try:
            return json.loads(getattr(self, col) or 'null')
        except (json.JSONDecodeError, TypeError):
            return None

    def get_progress(self):
        return self._decode('progress_json') or {}

    def set_progress(self, d):
        self.progress_json = json.dumps(d or {}, ensure_ascii=False)

    def get_results(self):
        return self._decode('results_json') or []

    def set_results(self, items):
        self.results_json = json.dumps(items or [], ensure_ascii=False)

    def get_skipped(self):
        return self._decode('skipped_json') or []

    def set_skipped(self, items):
        self.skipped_json = json.dumps(items or [], ensure_ascii=False)

    def get_errors(self):
        return self._decode('errors_json') or []

    def append_error(self, entry):
        items = self.get_errors()
        items.append(entry)
        self.errors_json = json.dumps(items, ensure_ascii=False)


class TeacherDashboardLayout(db.Model):
    """Per-(teacher, class) widget layout for the My Class insights page.

    layout_json is a list of {key, x, y, w, h} dicts emitted by GridStack;
    we don't validate its internal shape here so future widgets can extend
    it without a migration. The unique constraint guarantees one layout
    per (teacher, class) pair so we can upsert without searching."""
    __tablename__ = 'teacher_dashboard_layout'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=False, index=True)
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), nullable=False, index=True)
    layout_json = db.Column(db.Text, nullable=False, default='[]')
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint('teacher_id', 'class_id', name='uq_dashboard_teacher_class'),
    )


class ExemplarAnalysisLog(db.Model):
    """Append-only history of every exemplar-analysis run for an
    assignment. The current/latest analysis still lives on
    Assignment.exemplar_analysis_json (used by the exemplars page).
    This table additionally captures every run over time so we can
    later cluster recurring misconception themes across assignments
    / classes / terms.

    Deliberately separate from FeedbackEdit / MarkingPrinciples /
    CategorisationCorrection — those are about per-question grading
    consistency; this is about teacher-discussion-grade pattern
    surfacing across an entire class.

    Slim schema by design: subject / assign_type / provider / model /
    teacher_id are all reachable via JOIN to assignments. Only the
    snapshot data (submissions_count, roster_size at run time) and
    the full areas_json output are stored here."""
    __tablename__ = 'exemplar_analysis_log'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    assignment_id = db.Column(
        db.String(36), db.ForeignKey('assignments.id'),
        nullable=False, index=True,
    )
    # How many submissions the AI saw this run (capped at 40 by the
    # route). Distinct from roster_size because the route samples.
    submissions_count = db.Column(db.Integer, nullable=True)
    # Class size at run time. Nullable because backfilled rows from
    # before this table existed don't have a reliable historical value.
    roster_size = db.Column(db.Integer, nullable=True)
    areas_json = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False, index=True,
    )
    # NULL = current/latest analysis for this assignment (eligible for
    # clustering rollups). Non-NULL = an older analysis that a re-run
    # has since replaced; kept for audit / drift study but excluded
    # from any "current state" aggregations. The teacher_exemplars_generate
    # route stamps this on existing rows for the same assignment before
    # inserting the new latest one.
    superseded_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)


class DepartmentDashboardLayout(db.Model):
    """One layout per viewer for the department insights dashboard.

    The same layout is reused across every (dept × level) tab — only the
    data swaps when the tab changes. `last_level` is the level tab the
    viewer was last on (Sec 1 / 2 / 3 / 4-5 / All); `last_dept_id` is
    the department tab (None = "All"). Both restored on next visit so
    they don't have to re-click."""
    __tablename__ = 'department_dashboard_layout'
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'),
                           primary_key=True)
    layout_json = db.Column(db.Text, nullable=False, default='[]')
    last_level = db.Column(db.String(20), default='sec1')
    last_dept_id = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DepartmentGoal(db.Model):
    """HOD / Subject Head / Lead-set goals shown in the dept_goals widget.

    Soft-deleted via `deleted_at`. `department_id`, `target_level`, and
    `target_subject` are all nullable: NULL means "applies to all
    departments / all levels / all subjects" respectively. The widget's
    query keeps a row when each nullable matches the current tab (or is
    NULL = wildcard). Subject Heads can only create goals whose
    `target_subject` is one of their teaching subjects (route-level)."""
    __tablename__ = 'department_goal'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    # one of: pass_rate, avg_score, submission_rate
    metric_type = db.Column(db.String(40), nullable=False)
    target_value = db.Column(db.Float, nullable=False)
    target_level = db.Column(db.String(20), nullable=True)
    target_subject = db.Column(db.String(200), nullable=True)
    department_id = db.Column(db.Integer,
                              db.ForeignKey('departments.id', ondelete='SET NULL'),
                              nullable=True)
    created_by_id = db.Column(db.String(36), db.ForeignKey('teachers.id'),
                              nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
