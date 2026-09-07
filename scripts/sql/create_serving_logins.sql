-- Two least-privilege logins for the SQL Server serving database.
--
-- `sa` was a bootstrap credential, not a design. It let the web application drop
-- tables and the publisher read every database on the instance, and the only thing
-- standing between a mistyped query and the serving copy was that nobody had made
-- the mistake yet. This script replaces it with two logins that can each do exactly
-- one job:
--
--   vn_air_web      SELECT on dbo, and nothing else. The Hikari pool already
--                   declares `read-only: true`; this states the same boundary
--                   somewhere the application cannot override.
--   vn_air_publish  drop, create, index and fill the fifteen serving tables. It is
--                   the only writer, which is the invariant the whole two-database
--                   design rests on.
--
-- **No password appears in this file, and none can.** Run it through
-- `scripts\create_serving_logins.ps1`, which reads the two plaintext passwords from
-- the environment (or prompts for them without echo) and passes them here hex-encoded.
--
-- **Why hex, and not the plaintext through a scripting variable.** sqlcmd substitutes
-- a scripting variable *textually*, before SQL Server parses anything. Written the
-- obvious way -- DECLARE @pw nvarchar(128) = N'$(WEB_PASSWORD)' -- a password
-- containing a single quote closes that literal early and the rest of the password
-- becomes SQL. Dynamic-SQL quoting further down cannot help: the damage is done one
-- layer above it, in a pass that has no idea it is handling a string. This was not
-- theoretical here; it is how the first version of this script failed, on a generated
-- password whose punctuation was chosen to find exactly that class of defect. Hex has
-- no character that can end a literal, so the failure is structurally unavailable
-- rather than merely avoided.
--
-- Re-running is safe and is how a password is rotated: a login that already exists is
-- altered rather than recreated, so its database user, and every grant made to that
-- user, survives. Recreating the login would orphan the user, and the next publish
-- would fail as a login that authenticates and can then see nothing -- an error that
-- names permissions and points at the wrong cause.
--
-- The wrapper connects with -E, the Windows administrator already at the keyboard.
-- Nothing here needs `sa`, and asking for it would reintroduce the credential this
-- script exists to retire.

:on error exit

USE [master];
GO

-- An undefined scripting variable is left by sqlcmd as the literal text $(NAME),
-- which would otherwise be decoded into a password nobody knows on a login nobody
-- can use. The marker is assembled from CHAR(36) so this check does not itself look
-- like a variable reference to the substitution pass, which runs first.
DECLARE @unsubstituted nvarchar(4) = N'%' + CHAR(36) + N'(%';
DECLARE @web_hex nvarchar(1024) = N'$(WEB_PASSWORD_HEX)';
DECLARE @pub_hex nvarchar(1024) = N'$(PUBLISH_PASSWORD_HEX)';

IF @web_hex LIKE @unsubstituted OR LEN(@web_hex) = 0
    THROW 50001, 'WEB_PASSWORD_HEX was not supplied. Run scripts\create_serving_logins.ps1, not this file.', 1;
IF @pub_hex LIKE @unsubstituted OR LEN(@pub_hex) = 0
    THROW 50002, 'PUBLISH_PASSWORD_HEX was not supplied. Run scripts\create_serving_logins.ps1, not this file.', 1;
IF @web_hex = @pub_hex
    THROW 50003, 'The two logins must not share a password; separating them is the point.', 1;

-- Style 2 parses hex without an 0x prefix. The wrapper encodes UTF-16LE, which is what
-- nvarchar holds, so the round trip returns the password unchanged rather than a
-- transliteration of it.
DECLARE @web_password nvarchar(128) = CONVERT(nvarchar(128), CONVERT(varbinary(256), @web_hex, 2));
DECLARE @pub_password nvarchar(128) = CONVERT(nvarchar(128), CONVERT(varbinary(256), @pub_hex, 2));

IF LEN(@web_password) = 0 OR LEN(@pub_password) = 0
    THROW 50004, 'A password decoded to nothing. Check the hex encoding in the wrapper.', 1;

-- The serving database, if this is a machine that has never had one. The publisher
-- connects straight to it and reports an authentication-shaped error when it is
-- absent, which sends the reader looking in the wrong place.
IF DB_ID(N'vn_air_quality') IS NULL
    CREATE DATABASE [vn_air_quality];

-- CREATE LOGIN cannot parameterise its password, so the statement is assembled here.
-- QUOTENAME with a quote delimiter doubles any embedded quote. This is the layer that
-- *can* be quoted correctly, because by now the password is a value in a variable
-- rather than text waiting to be spliced into a script.
--
-- CHECK_POLICY stays ON: these are the credentials for the only writer to the serving
-- copy, and a weak one should fail here rather than be accepted quietly.
-- CHECK_EXPIRATION stays OFF, because an expired password on an unattended publisher
-- is an outage whose error message names authentication and never mentions the calendar.
DECLARE @sql nvarchar(max);

IF SUSER_ID(N'vn_air_web') IS NULL
    SET @sql = N'CREATE LOGIN [vn_air_web] WITH PASSWORD = ' + QUOTENAME(@web_password, '''')
             + N', DEFAULT_DATABASE = [vn_air_quality], CHECK_POLICY = ON, CHECK_EXPIRATION = OFF';
ELSE
    SET @sql = N'ALTER LOGIN [vn_air_web] WITH PASSWORD = ' + QUOTENAME(@web_password, '''');
EXEC sys.sp_executesql @sql;

IF SUSER_ID(N'vn_air_publish') IS NULL
    SET @sql = N'CREATE LOGIN [vn_air_publish] WITH PASSWORD = ' + QUOTENAME(@pub_password, '''')
             + N', DEFAULT_DATABASE = [vn_air_quality], CHECK_POLICY = ON, CHECK_EXPIRATION = OFF';
ELSE
    SET @sql = N'ALTER LOGIN [vn_air_publish] WITH PASSWORD = ' + QUOTENAME(@pub_password, '''');
EXEC sys.sp_executesql @sql;
GO

USE [vn_air_quality];
GO

IF USER_ID(N'vn_air_web') IS NULL
    CREATE USER [vn_air_web] FOR LOGIN [vn_air_web];
IF USER_ID(N'vn_air_publish') IS NULL
    CREATE USER [vn_air_publish] FOR LOGIN [vn_air_publish];
GO

-- The reader. SELECT on the schema rather than on fifteen named tables, because the
-- publisher drops and recreates every one of them on every run: a grant made to a
-- table object dies with that object, and the page would go dark the first time the
-- five-minute timer ticked. A schema-level grant outlives the tables inside it.
GRANT CONNECT TO [vn_air_web];
GRANT SELECT ON SCHEMA::[dbo] TO [vn_air_web];

-- Withholding a permission and denying it are not the same thing. Granting only
-- SELECT already leaves this login unable to write; the DENY is what keeps it that
-- way after somebody adds it to db_datawriter to fix an unrelated problem, because
-- DENY beats every GRANT it meets. A read-only boundary this application depends on
-- is worth one line that cannot be undone by accident.
DENY INSERT, UPDATE, DELETE, ALTER, REFERENCES ON SCHEMA::[dbo] TO [vn_air_web];
GO

-- The writer. publish_serving_marts drops each table, recreates it, fills it and then
-- indexes it, inside one transaction, and rewrites the single control row in
-- dbo.warehouse_version. That is CREATE TABLE at the database level plus ALTER on the
-- schema, which is what carries DROP TABLE and CREATE INDEX with it.
--
-- DELETE is absent on purpose. A full reload has nothing to delete -- the table is
-- dropped, not emptied -- so granting it would only widen what a defect could reach.
GRANT CONNECT TO [vn_air_publish];
GRANT CREATE TABLE TO [vn_air_publish];
GRANT ALTER, SELECT, INSERT, UPDATE ON SCHEMA::[dbo] TO [vn_air_publish];
GO

PRINT 'vn_air_web and vn_air_publish are configured on vn_air_quality.';
PRINT 'Verify the boundary with: python scripts/verify_serving_logins.py';
GO
