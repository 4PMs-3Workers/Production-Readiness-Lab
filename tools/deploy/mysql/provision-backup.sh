#!/bin/bash
# Explicit operator operation for an existing volume; never enable xtrace.
set +x
set -eu
: "${PRL_BACKUP_PASSWORD:?Missing external backup password}"
: "${MYSQL_ROOT_PASSWORD:?Missing local administrator password}"
password_hex="$(printf '%s' "$PRL_BACKUP_PASSWORD" | od -An -v -tx1 | tr -d ' \n')"
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" timeout 30s mysql --protocol=socket -uroot --connect-timeout=5 <<SQL
SET @secret = CONVERT(0x${password_hex} USING utf8mb4);
SET @statement = CONCAT('CREATE USER IF NOT EXISTS ''prl_backup''@''%'' IDENTIFIED BY ', QUOTE(@secret));
PREPARE backup_identity FROM @statement;
EXECUTE backup_identity;
DEALLOCATE PREPARE backup_identity;
SET @statement = CONCAT('ALTER USER ''prl_backup''@''%'' IDENTIFIED BY ', QUOTE(@secret));
PREPARE backup_identity FROM @statement;
EXECUTE backup_identity;
DEALLOCATE PREPARE backup_identity;
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'prl_backup'@'%';
GRANT SELECT, TRIGGER, EVENT ON prl.* TO 'prl_backup'@'%';
GRANT BACKUP_ADMIN, SHOW_ROUTINE ON *.* TO 'prl_backup'@'%';
SQL
