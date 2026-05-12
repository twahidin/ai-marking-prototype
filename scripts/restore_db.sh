#!/usr/bin/env bash
# UP-07: Restore a single .sql.gz dump produced by backup_db.sh.
#
# Usage:
#   ./scripts/restore_db.sh s3://aifb-prod-backups/daily/aifb-...-YYYY-MM-DD....sql.gz
#
# Connects to TARGET_DATABASE_URL (separate from DATABASE_URL so a fat-finger
# can't clobber production). Print a banner and ASK BEFORE WRITING.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <s3-or-b2-uri-of-dump>" >&2
  exit 1
fi

if [[ -z "${TARGET_DATABASE_URL:-}" ]]; then
  echo "ERROR: TARGET_DATABASE_URL not set." >&2
  echo "       Use the scratch / staging DB URL — never production." >&2
  exit 1
fi

SRC="$1"
TMP_FILE="$(mktemp -t aifb-restore-XXXXXX.sql.gz)"
trap 'rm -f "$TMP_FILE"' EXIT

if [[ -n "${BACKUP_B2_ENDPOINT:-}" ]]; then
  ENDPOINT_ARG=(--endpoint-url "$BACKUP_B2_ENDPOINT")
else
  ENDPOINT_ARG=()
fi

echo "Downloading $SRC ..."
aws "${ENDPOINT_ARG[@]}" s3 cp "$SRC" "$TMP_FILE" --no-progress

echo
echo "About to RESTORE into:"
echo "  $TARGET_DATABASE_URL"
echo "All existing tables will be DROPPED (pg_dump --clean --if-exists)."
read -r -p "Type 'YES' to continue: " CONFIRM
if [[ "$CONFIRM" != "YES" ]]; then
  echo "Aborted."
  exit 1
fi

echo "Restoring ..."
gunzip -c "$TMP_FILE" | psql "$TARGET_DATABASE_URL"
echo "OK: restore complete."
