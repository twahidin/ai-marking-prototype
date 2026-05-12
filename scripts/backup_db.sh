#!/usr/bin/env bash
# UP-07: Daily PostgreSQL backup to object storage.
#
# Reads DATABASE_URL from env, runs pg_dump, gzips the output, and uploads
# to either AWS S3 (BACKUP_S3_BUCKET) or Backblaze B2 (BACKUP_B2_BUCKET via
# the s3-compatible endpoint).
#
# Schedule this via Railway Cron (Settings → Cron) or a GitHub Actions
# workflow. Apply retention as a bucket-side lifecycle rule, not here.
#
# Required env:
#   DATABASE_URL          postgres://user:pass@host:port/dbname
#   BACKUP_S3_BUCKET      e.g. s3://aifb-prod-backups   (one of these two)
#   BACKUP_B2_BUCKET      e.g. s3://aifb-prod-backups
#   BACKUP_B2_ENDPOINT    e.g. https://s3.eu-central-003.backblazeb2.com (B2 only)
#   AWS_ACCESS_KEY_ID     access key for the chosen provider
#   AWS_SECRET_ACCESS_KEY secret for the chosen provider
#
# Optional env:
#   BACKUP_PREFIX         path prefix inside the bucket (default: "daily")
#   BACKUP_LABEL          extra tag in the filename (default: hostname)

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL not set" >&2
  exit 1
fi

if [[ -z "${BACKUP_S3_BUCKET:-}" && -z "${BACKUP_B2_BUCKET:-}" ]]; then
  echo "ERROR: set BACKUP_S3_BUCKET or BACKUP_B2_BUCKET" >&2
  exit 1
fi

BACKUP_PREFIX="${BACKUP_PREFIX:-daily}"
BACKUP_LABEL="${BACKUP_LABEL:-$(hostname -s 2>/dev/null || echo unknown)}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
FILENAME="aifb-${BACKUP_LABEL}-${TIMESTAMP}.sql.gz"
TMP_FILE="$(mktemp -t aifb-backup-XXXXXX.sql.gz)"
trap 'rm -f "$TMP_FILE"' EXIT

echo "Dumping database to $TMP_FILE ..."
pg_dump --no-owner --no-privileges --clean --if-exists "$DATABASE_URL" \
  | gzip --best > "$TMP_FILE"

SIZE_BYTES=$(wc -c < "$TMP_FILE" | tr -d ' ')
echo "Dump complete: $SIZE_BYTES bytes."

if [[ "$SIZE_BYTES" -lt 1024 ]]; then
  echo "ERROR: dump is suspiciously small (<1KB). Aborting upload." >&2
  exit 2
fi

if [[ -n "${BACKUP_B2_BUCKET:-}" ]]; then
  DEST="${BACKUP_B2_BUCKET%/}/${BACKUP_PREFIX}/${FILENAME}"
  ENDPOINT_ARG=(--endpoint-url "$BACKUP_B2_ENDPOINT")
else
  DEST="${BACKUP_S3_BUCKET%/}/${BACKUP_PREFIX}/${FILENAME}"
  ENDPOINT_ARG=()
fi

echo "Uploading to $DEST ..."
aws "${ENDPOINT_ARG[@]}" s3 cp "$TMP_FILE" "$DEST" --no-progress
echo "OK: $DEST"
