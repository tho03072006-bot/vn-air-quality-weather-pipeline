"""Prove the two serving logins can each do their job and nothing else.

    python scripts/verify_serving_logins.py

A GRANT statement that ran without error says what was requested, not what was
granted. Permissions in SQL Server compose: role membership, schema-level grants,
ownership chains and database-level rights all meet in the effective permission set,
and the only honest way to know what a login can do is to connect as that login and
try. So this asks both sides of every boundary:

* `vn_air_web` must read, and must be refused every way of writing. A read-only
  application whose credential can write is read-only by convention, and convention
  is what the Hikari `read-only: true` flag already provides. The point of a separate
  login is a guarantee that survives a mistake in the application.
* `vn_air_publish` must be able to drop, create, index, fill and read, because those
  are exactly the operations `publish_serving_marts` performs inside one transaction.
  A writer missing one of them fails at 03:00 on a Sunday, halfway through a publish.

**A refusal is a pass, and only for the right reason.** A test that accepts any
exception passes when the server is unreachable, when the password is wrong, and when
the table does not exist -- three states that have nothing to do with permissions.
Every refusal here is matched against SQL Server's own permission-denied error
classes, so an authentication failure is reported as a failure rather than banked as
proof of a boundary that was never tested.

**The writer's checks are made on a scratch table, not on a serving relation.** A
verification that proves the publisher can write by writing into `dbo.dim_province`
has corrupted the thing it was verifying. The scratch table is created and dropped
inside this run, which incidentally exercises the two DDL rights the publisher most
depends on.

Passwords come from `VN_AIR_WEB_PASSWORD` and `VN_AIR_PUBLISH_PASSWORD` in the
environment and are never printed, logged or included in a failure message.

Exit code is 0 when every boundary holds and 1 otherwise, so this can gate a
deployment rather than only inform a reader.
"""

from __future__ import annotations

import os
import sys

import pyodbc

from vn_air_quality_weather.settings import Settings

WEB_LOGIN = "vn_air_web"
PUBLISH_LOGIN = "vn_air_publish"

# SQL Server reports an unmet permission with one of these. 229 and 230 are the
# object- and column-level denials, 262 is a database-level right the login does not
# hold (CREATE TABLE), 297 covers the ownership-chain case, and 15247 is the
# server-level refusal CREATE LOGIN raises. Matching on the code rather than on
# message text keeps the check working on a server whose messages are localised --
# this machine's SQL Server would otherwise be checked against English strings it
# might never emit.
PERMISSION_DENIED_CODES = frozenset({229, 230, 262, 297, 300, 15247})

# DROP TABLE is the exception, and it is the reason this file has a second kind of
# check. SQL Server answers a forbidden drop with 3701 -- "because it does not exist
# or you do not have permission" -- deliberately conflating the two so that a probe
# cannot map out a schema it has no rights to. Accepting 3701 as proof of a boundary
# would therefore pass just as happily against a table that was never there, which is
# an empty gate wearing the costume of a full one. `expect_drop_denied` establishes
# that the login can see the table before it accepts the refusal.
DROP_AMBIGUOUS_CODE = 3701

# A table that does not belong to the serving schema and cannot collide with a mart.
SCRATCH_TABLE = "dbo.zz_verify_serving_logins"


class Outcome:
    """One boundary check and what happened when it was tried."""

    def __init__(self, label: str, ok: bool, detail: str) -> None:
        self.label = label
        self.ok = ok
        self.detail = detail


def connection_string(settings: Settings, user: str, password: str) -> str:
    """Build an ODBC string for one login, reusing the project's own spelling.

    `Settings.require_sqlserver_odbc` is the single place this project assembles a
    connection, including the brace doubling a generated password needs. Copying it
    here would be a second spelling free to drift from the first, so the settings
    object is cloned with the credential replaced instead.
    """

    return settings.model_copy(
        update={"sqlserver_user": user, "sqlserver_password": _secret(password)}
    ).require_sqlserver_odbc()


def _secret(value: str):
    from pydantic import SecretStr

    return SecretStr(value)


def required_env(name: str) -> str:
    """Return an environment value, or explain what to run rather than failing bare."""

    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(
            f"{name} is not set.\n"
            "Set it in the environment, or create the logins with:\n"
            "    powershell -ExecutionPolicy Bypass -File \\\n"
            "        scripts\\create_serving_logins.ps1 -Persist"
        )
    return value


def denied(error: pyodbc.Error) -> bool:
    """Report whether this error is SQL Server refusing a permission.

    pyodbc carries the native error code in the message rather than in a field, so it
    is read out of the driver's diagnostic record. Anything that is not a recognised
    denial -- a login failure, an unreachable server, a missing table -- is not a
    boundary holding, and returning False here is what turns it into a reported
    failure instead of a silent pass.
    """

    for code in PERMISSION_DENIED_CODES:
        if f"({code})" in str(error):
            return True
    return False


def expect_denied(cursor: pyodbc.Cursor, label: str, statement: str) -> Outcome:
    """Run a statement that must be refused, and say why the refusal counted."""

    try:
        cursor.execute(statement)
    except pyodbc.Error as error:
        if denied(error):
            return Outcome(label, True, "refused by SQL Server")
        return Outcome(
            label, False, f"failed for a reason that is not a denial: {error.__class__.__name__}"
        )
    else:
        cursor.connection.rollback()
        return Outcome(label, False, "SUCCEEDED -- this login can do something it must not")


def expect_drop_denied(cursor: pyodbc.Cursor, label: str, table: str) -> Outcome:
    """Prove a drop was refused for want of permission, not for want of a table.

    The table is read first. A login holding SELECT on it has established that the
    object exists and is visible to this principal, which is exactly the ambiguity
    3701 leaves open; only then does a 3701 on the drop mean what this check needs it
    to mean. Without the read, this check would pass on a machine where the publish
    has never run.
    """

    try:
        cursor.execute(f"SELECT TOP 1 1 FROM {table}").fetchall()
    except pyodbc.Error as error:
        return Outcome(
            label, False, f"cannot confirm the table exists, so a refusal proves nothing: {error}"
        )

    try:
        cursor.execute(f"DROP TABLE {table}")
    except pyodbc.Error as error:
        if f"({DROP_AMBIGUOUS_CODE})" in str(error) or denied(error):
            return Outcome(label, True, "refused, on a table this login can read")
        return Outcome(label, False, f"failed for a reason that is not a denial: {error}")
    else:
        cursor.connection.rollback()
        return Outcome(label, False, "SUCCEEDED -- this login can drop a serving relation")


def expect_allowed(cursor: pyodbc.Cursor, label: str, statement: str) -> Outcome:
    """Run a statement that must succeed, and report the error verbatim when it does not."""

    try:
        cursor.execute(statement)
    except pyodbc.Error as error:
        return Outcome(label, False, f"refused: {error}")
    return Outcome(label, True, "allowed")


def check_reader(settings: Settings, password: str) -> list[Outcome]:
    """Read once, then try every way there is to write."""

    outcomes: list[Outcome] = []
    with pyodbc.connect(connection_string(settings, WEB_LOGIN, password), autocommit=False) as sql:
        cursor = sql.cursor()

        # The one thing it must be able to do. Checked first, so a login that can do
        # nothing at all is reported as broken rather than as admirably secure.
        try:
            row = cursor.execute("SELECT COUNT(*) FROM dbo.dim_province").fetchone()
            outcomes.append(Outcome("SELECT a serving mart", True, f"{row[0]} rows"))
        except pyodbc.Error as error:
            outcomes.append(Outcome("SELECT a serving mart", False, f"refused: {error}"))

        try:
            row = cursor.execute("SELECT version_id FROM dbo.warehouse_version").fetchone()
            outcomes.append(Outcome("SELECT the control row", True, f"version {row[0]}"))
        except pyodbc.Error as error:
            outcomes.append(Outcome("SELECT the control row", False, f"refused: {error}"))

        outcomes.append(
            expect_denied(
                cursor,
                "INSERT into a serving mart",
                "INSERT INTO dbo.dim_province (province_code) VALUES ('ZZ')",
            )
        )
        outcomes.append(
            expect_denied(
                cursor,
                "UPDATE the control row",
                "UPDATE dbo.warehouse_version SET version_id = version_id + 1",
            )
        )
        outcomes.append(
            expect_denied(cursor, "DELETE from a serving mart", "DELETE FROM dbo.dim_province")
        )
        outcomes.append(
            expect_drop_denied(cursor, "DROP a serving mart", "dbo.mart_raw_source_health")
        )
        outcomes.append(
            expect_denied(
                cursor,
                "CREATE a table",
                f"CREATE TABLE {SCRATCH_TABLE}_web (id int)",
            )
        )
        sql.rollback()
    return outcomes


def check_writer(settings: Settings, password: str) -> list[Outcome]:
    """Exercise exactly the operations one publish performs, on a scratch table."""

    outcomes: list[Outcome] = []
    with pyodbc.connect(
        connection_string(settings, PUBLISH_LOGIN, password), autocommit=True
    ) as sql:
        cursor = sql.cursor()
        cursor.execute(
            f"IF OBJECT_ID(N'{SCRATCH_TABLE}', 'U') IS NOT NULL DROP TABLE {SCRATCH_TABLE}"
        )

        outcomes.append(
            expect_allowed(
                cursor,
                "CREATE TABLE",
                f"CREATE TABLE {SCRATCH_TABLE} (id int NOT NULL, label nvarchar(16))",
            )
        )
        outcomes.append(
            expect_allowed(
                cursor, "INSERT", f"INSERT INTO {SCRATCH_TABLE} (id, label) VALUES (1, 'x')"
            )
        )
        outcomes.append(
            expect_allowed(
                cursor,
                "CREATE INDEX",
                f"CREATE UNIQUE INDEX IX_zz_verify ON {SCRATCH_TABLE} (id)",
            )
        )
        outcomes.append(
            expect_allowed(
                cursor,
                "UPDATE the control row",
                "UPDATE dbo.warehouse_version SET row_count = row_count",
            )
        )
        outcomes.append(
            expect_allowed(cursor, "SELECT a serving mart", "SELECT COUNT(*) FROM dbo.dim_province")
        )
        outcomes.append(expect_allowed(cursor, "DROP TABLE", f"DROP TABLE {SCRATCH_TABLE}"))

        # Not needed by any publish, and therefore not granted. Checked because the
        # narrow grant is the claim being made: a writer that quietly holds
        # db_owner would pass every check above and none of the ones that matter.
        outcomes.append(
            expect_denied(
                cursor,
                "CREATE a login (server-level)",
                "CREATE LOGIN zz_verify_should_fail WITH PASSWORD = 'Nope-12345678'",
            )
        )
    return outcomes


def report(title: str, outcomes: list[Outcome]) -> int:
    """Print one login's results and return how many checks failed."""

    print(f"\n{title}")
    width = max(len(o.label) for o in outcomes)
    failed = 0
    for outcome in outcomes:
        mark = "OK  " if outcome.ok else "FAIL"
        print(f"  {mark}  {outcome.label:<{width}}  {outcome.detail}")
        if not outcome.ok:
            failed += 1
    return failed


def main() -> int:
    settings = Settings()
    web_password = required_env("VN_AIR_WEB_PASSWORD")
    publish_password = required_env("VN_AIR_PUBLISH_PASSWORD")

    print(
        f"Checking the serving logins on {settings.sqlserver_host}:{settings.sqlserver_port}"
        f"/{settings.sqlserver_database}"
    )

    failed = 0
    failed += report(
        f"{WEB_LOGIN} -- must read, and must be refused every write",
        check_reader(settings, web_password),
    )
    failed += report(
        f"{PUBLISH_LOGIN} -- must perform one publish, and no more",
        check_writer(settings, publish_password),
    )

    if failed:
        print(f"\n{failed} check(s) failed. The permission boundary is not what it claims.")
        return 1
    print("\nBoth logins hold exactly the rights they were granted, and no others.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
