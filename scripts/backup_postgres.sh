#!/usr/bin/env bash
# ==============================================================================
# Nightly PostgreSQL Database Backup Script for Bandeh
# ==============================================================================
# Creates a compressed, timestamped pg_dump of the PostgreSQL database.
# Retains the last 14 days of backups and automatically prunes older files.
#
# Usage:
#   ./scripts/backup_postgres.sh
#   Or add to crontab:
#   0 3 * * * /home/mhb/app/coding/easy-auto/scripts/backup_postgres.sh >> /var/log/bandeh_backup.log 2>&1
# ==============================================================================

set -euo pipefail

# Determine script and repository root directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load environment configuration from .env if present
if [[ -f "$REPO_ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
fi

# Target settings with fallbacks
POSTGRES_USER="${POSTGRES_USER:-directus}"
POSTGRES_DB="${POSTGRES_DB:-directus_db}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
BACKUP_FILENAME="backup_${POSTGRES_DB}_${TIMESTAMP}.sql.gz"
BACKUP_FILEPATH="${BACKUP_DIR}/${BACKUP_FILENAME}"

mkdir -p "$BACKUP_DIR"

echo "=== [$(date --iso-8601=seconds)] Starting PostgreSQL Backup for '${POSTGRES_DB}' ==="

# Execute pg_dump via running docker container or local CLI
if docker ps --format '{{.Names}}' | grep -q "^fastapi-e-commerce-db$"; then
    echo "Dumping via Docker container 'fastapi-e-commerce-db'..."
    docker exec \
        -e PGPASSWORD="$POSTGRES_PASSWORD" \
        fastapi-e-commerce-db \
        pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges \
        | gzip -9 > "$BACKUP_FILEPATH"
elif command -v pg_dump >/dev/null 2>&1; then
    echo "Dumping via host pg_dump client..."
    PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
        -h "${POSTGRES_HOST:-localhost}" \
        -p "${POSTGRES_PORT:-5432}" \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        --clean --if-exists --no-owner --no-privileges \
        | gzip -9 > "$BACKUP_FILEPATH"
else
    echo "ERROR: Neither running 'fastapi-e-commerce-db' container nor local 'pg_dump' was found." >&2
    exit 1
fi

# Verify backup archive exists and is non-empty
if [[ ! -s "$BACKUP_FILEPATH" ]]; then
    echo "ERROR: Generated backup file is empty or missing: $BACKUP_FILEPATH" >&2
    exit 1
fi

FILE_SIZE="$(du -h "$BACKUP_FILEPATH" | cut -f1)"
echo "Backup created successfully: $BACKUP_FILEPATH ($FILE_SIZE)"

# Prune backups older than retention window
echo "Pruning backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -type f -name "backup_${POSTGRES_DB}_*.sql.gz" -mtime +"$RETENTION_DAYS" -exec rm -v {} \;

echo "=== Backup completed successfully at $(date --iso-8601=seconds) ==="
