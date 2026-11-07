#!/bin/bash
# Explicit post-migration operation. Root secret stays inside the database container.
set -eu
export MYSQL_PWD="${MYSQL_ROOT_PASSWORD:?Missing MySQL root password}"
exec timeout 30s mysql --no-defaults --protocol=socket --host=localhost \
    --socket=/var/run/mysqld/mysqld.sock -uroot --connect-timeout=5 < /opt/prl/grants.sql
