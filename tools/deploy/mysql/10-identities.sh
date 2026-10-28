#!/bin/bash
# Run by the official MySQL entrypoint on a NEW volume only. Never enable xtrace.
(
set -eu
: "${PRL_MIGRATION_PASSWORD:?Missing migration password}"
: "${PRL_RUNTIME_PASSWORD:?Missing runtime password}"
: "${PRL_SEED_PASSWORD:?Missing seed password}"
# Encode secrets rather than interpolating them into SQL quoting. mysql_process_sql
# consumes stdin without printing it. Root remains socket-local (no MYSQL_ROOT_HOST).
mysql_process_sql() {
    MYSQL_PWD="$MYSQL_ROOT_PASSWORD" timeout 30s mysql --protocol=socket -uroot --connect-timeout=5
}
create_identity() {
    local identity="$1" password_hex
    password_hex="$(printf '%s' "$2" | od -An -v -tx1 | tr -d ' \n')"
    mysql_process_sql <<SQL
SET @secret = CONVERT(0x${password_hex} USING utf8mb4);
SET @statement = CONCAT('CREATE USER ''${identity}''@''%'' IDENTIFIED BY ', QUOTE(@secret));
PREPARE identity_statement FROM @statement;
EXECUTE identity_statement;
DEALLOCATE PREPARE identity_statement;
SQL
}
create_identity prl_migration "$PRL_MIGRATION_PASSWORD"
create_identity prl_runtime "$PRL_RUNTIME_PASSWORD"
create_identity prl_seed "$PRL_SEED_PASSWORD"
if [ -n "${PRL_BACKUP_PASSWORD:-}" ]; then
    create_identity prl_backup "$PRL_BACKUP_PASSWORD"
    mysql_process_sql <<'SQL'
GRANT SELECT ON prl.* TO 'prl_backup'@'%';
GRANT BACKUP_ADMIN ON *.* TO 'prl_backup'@'%';
SQL
fi
mysql_process_sql <<'SQL'
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES ON prl.* TO 'prl_migration'@'%';
SQL
)
