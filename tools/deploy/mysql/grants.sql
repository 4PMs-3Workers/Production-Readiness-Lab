-- Reapply exact privileges after explicit migration. No schema/global runtime DML:
-- adding a future table cannot silently grant mutation access to it.
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'prl_runtime'@'%';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'prl_seed'@'%';
GRANT SELECT, UPDATE ON prl.users TO 'prl_runtime'@'%';
GRANT SELECT, INSERT, UPDATE ON prl.sessions TO 'prl_runtime'@'%';
GRANT SELECT, INSERT, UPDATE ON prl.appointments TO 'prl_runtime'@'%';
GRANT SELECT, INSERT, UPDATE ON prl.change_proposals TO 'prl_runtime'@'%';
GRANT SELECT, INSERT, UPDATE ON prl.notifications TO 'prl_runtime'@'%';
GRANT SELECT, INSERT ON prl.audit_events TO 'prl_runtime'@'%';
GRANT SELECT ON prl.purposes TO 'prl_runtime'@'%';
GRANT SELECT ON prl.service_settings TO 'prl_runtime'@'%';
GRANT SELECT ON prl.service_hours TO 'prl_runtime'@'%';
GRANT SELECT, INSERT, UPDATE ON prl.staff_purposes TO 'prl_runtime'@'%';
GRANT SELECT, INSERT, DELETE ON prl.staff_unavailability TO 'prl_runtime'@'%';
GRANT SELECT, INSERT ON prl.users TO 'prl_seed'@'%';
GRANT SELECT, INSERT ON prl.purposes TO 'prl_seed'@'%';
GRANT SELECT, INSERT ON prl.staff_purposes TO 'prl_seed'@'%';
GRANT SELECT, INSERT ON prl.service_settings TO 'prl_seed'@'%';
GRANT SELECT, INSERT ON prl.service_hours TO 'prl_seed'@'%';
-- Release gate becomes readable only after every runtime/seed grant succeeds.
GRANT SELECT ON prl.alembic_version TO 'prl_runtime'@'%', 'prl_seed'@'%';
