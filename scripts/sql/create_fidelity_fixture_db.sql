-- The isolated database the Java fidelity suite reads.
--
-- vn-air-web's RepositoryFidelityTests used to compare its T-SQL against the live
-- serving database, which the publish timer rewrites every five minutes. Five of the
-- served relations are frozen copies of views over current_timestamp, so the live copy
-- moved underneath a fixture that could not, and the suite went red within the hour with
-- nothing changed in either codebase. This database holds one frozen snapshot instead,
-- published by the real publisher from data/fixtures/serving_snapshot, and nothing
-- writes it except scripts/export_query_fingerprints.py --load-fixture-db.
--
-- Permissions mirror the serving database exactly, so the suite reads through the same
-- boundary the application does: vn_air_publish may build the tables, vn_air_web may
-- only read them and is DENYed every write.
--
-- Run once, as the Windows administrator, after scripts\create_serving_logins.ps1 has
-- created the two logins:
--
--     sqlcmd -S tcp:localhost,1433 -E -C -b -i scripts\sql\create_fidelity_fixture_db.sql
--
-- Idempotent. It touches no other database: not vn_air_quality, and not the other
-- projects that share this instance.

:on error exit

USE [master];
GO

IF SUSER_ID(N'vn_air_web') IS NULL OR SUSER_ID(N'vn_air_publish') IS NULL
    THROW 50010, 'Create the serving logins first: scripts\create_serving_logins.ps1 -Persist', 1;

IF DB_ID(N'vn_air_quality_fidelity') IS NULL
    CREATE DATABASE [vn_air_quality_fidelity];
GO

USE [vn_air_quality_fidelity];
GO

IF USER_ID(N'vn_air_web') IS NULL
    CREATE USER [vn_air_web] FOR LOGIN [vn_air_web];
IF USER_ID(N'vn_air_publish') IS NULL
    CREATE USER [vn_air_publish] FOR LOGIN [vn_air_publish];
GO

GRANT CONNECT TO [vn_air_web];
GRANT SELECT ON SCHEMA::[dbo] TO [vn_air_web];
DENY INSERT, UPDATE, DELETE, ALTER, REFERENCES ON SCHEMA::[dbo] TO [vn_air_web];
GO

GRANT CONNECT TO [vn_air_publish];
GRANT CREATE TABLE TO [vn_air_publish];
GRANT ALTER, SELECT, INSERT, UPDATE ON SCHEMA::[dbo] TO [vn_air_publish];
GO

PRINT 'vn_air_quality_fidelity is ready. Load it with:';
PRINT '    python scripts/export_query_fingerprints.py --load-fixture-db --out ../vn-air-web/src/test/resources/query_fingerprints.json';
GO
