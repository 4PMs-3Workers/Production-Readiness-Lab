#!/bin/bash
# Explicit post-migration operation. Root secret stays inside the database container.
set -eu
export MYSQL_PWD="${MYSQL_ROOT_PASSWORD:?Missing MySQL root password}"
exec timeout 30s mysql --protocol=socket -uroot --connect-timeout=5 < /opt/prl/grants.sql
