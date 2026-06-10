"""End-to-end SQLite walkthrough for CentauroDB.

This file is BOTH a tutorial AND a CI smoke test:

  - Read it top-to-bottom to learn what every feature is for.
  - Run it (`uv run python examples/e2e/sqlite-project.py`)
    to verify the whole library still works on SQLite.
  - It is invoked by `.github/workflows/e2e.yml` on every push.

Each section follows the same shape:

  THE PAIN  — what you would write WITHOUT this feature, and why it hurts.
  THE FIX   — the CentauroDB equivalent.
  ASSERT    — invariants that must hold; failure aborts the script.

Domain: a tiny "research lab" — Experiments (1) → Trials (N), plus a
Sensor with a temperature time-series. Realistic enough to exercise
relations, views, indexes, time-series, and Polars in one story.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile

# Force UTF-8 on stdout so the walkthrough renders the same on Windows
# consoles (cp1252 by default) as it does on Linux/macOS CI runners.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timedelta, timezone
from pathlib import Path

from centaurodb import (
    AsyncCollection,
    CentauroModel,
    CentauroModelSeries,
    CentauroValues,
    Collection,
    Engine,
    ObjectAlreadyPersistedError,
    ObjectNotFoundError,
    TimeSeriesCollection,
    parent_ref,
    renamed_from,
)


# ─────────────────────────────────────────────────────────────────────
# Tiny output helpers — keeps the walkthrough readable in CI logs.
# ─────────────────────────────────────────────────────────────────────
def section(n: int, title: str) -> None:
    bar = "=" * 68
    print(f"\n{bar}\n  [{n}]  {title}\n{bar}")


def step(msg: str) -> None:
    print(f"  - {msg}")


def ok(msg: str) -> None:
    print(f"  [ok] {msg}")


# ═════════════════════════════════════════════════════════════════════
# §1 — Engine: pick a backend from a URL, no config files
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   Most ORMs make you wire a Settings class, env vars, an engine
#   factory, and a session-maker before you can write a single row.
#   Tutorials open with 30 lines of boilerplate.
# THE FIX (with):
#   `Engine(url)` decides backend from the URL scheme. SQLite needs no
#   server, no config — just a path or `:memory:`. Same surface as
#   Postgres later, when you outgrow embedded.
# ─────────────────────────────────────────────────────────────────────
def setup_engine() -> tuple[Engine, Path]:
    section(1, "Engine — zero-config SQLite")
    tmp = Path(tempfile.mkdtemp(prefix="cdb-e2e-")) / "lab.db"
    step(f"file: {tmp}")
    conn = Engine(f"sqlite:///{tmp}")
    ok("engine created (SQLite, file-based)")
    return conn, tmp


# ═════════════════════════════════════════════════════════════════════
# §2 — Models: Pydantic IS the schema
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   With SQLAlchemy + Pydantic you keep TWO definitions in sync: the
#   ORM table (column types, FK, server defaults) AND the Pydantic
#   model (validators, JSON shape). Drift is constant and silent.
# THE FIX (with):
#   `CentauroModel` is a Pydantic model that knows how to persist
#   itself. The class is the only source of truth. Storage is JSON,
#   so adding a field never needs a column migration.
# ─────────────────────────────────────────────────────────────────────
class Experiment(CentauroModel):
    __centauro_name__ = "experiment"
    code: str = ""
    title: str = ""
    status: str = "draft"  # draft | running | done


class Trial(CentauroModel):
    __centauro_name__ = "trial"
    experiment: int | None = parent_ref(Experiment)  # see §6
    label: str = ""
    duration_s: float = 0.0
    success: bool = False


# ═════════════════════════════════════════════════════════════════════
# §3 — CRUD: write, read, update, delete
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   A bare-SQL CRUD round-trip is: connect → cursor → INSERT with
#   placeholders → fetch lastrowid → SELECT by id → manual hydration
#   into a dict or namedtuple. Boring code, easy to get wrong.
# THE FIX (with):
#   `write_object` / `read_object_by_id` / `update_object` /
#   `delete_object` operate on Pydantic instances. No mapping layer.
# ─────────────────────────────────────────────────────────────────────
def crud_demo(conn: Engine) -> Collection:
    section(3, "CRUD — Pydantic instances in, Pydantic instances out")
    experiments = Collection(conn, "experiments")

    e1 = Experiment(code="EXP-001", title="Catalyst A", status="draft")
    experiments.write_object(e1)
    ok(f"wrote {e1.code} (id={e1._row.id})")

    # Round-trip
    loaded = experiments.read_object_by_id(e1._row.id, Experiment)
    assert loaded.title == "Catalyst A", "round-trip mismatch"
    ok("read_object_by_id round-trip OK")

    # Update
    e1.status = "running"
    experiments.update_object(e1)
    again = experiments.read_object_by_id(e1._row.id, Experiment)
    assert again.status == "running"
    ok("update_object reflected on read")

    # Duplicate-write guard
    try:
        experiments.write_object(e1)
    except ObjectAlreadyPersistedError:
        ok("typed exception: ObjectAlreadyPersistedError (re-write guarded)")
    else:
        raise AssertionError("expected ObjectAlreadyPersistedError")

    # Add a few more so later sections have something to query
    experiments.write_object(
        Experiment(code="EXP-002", title="Catalyst B", status="running")
    )
    experiments.write_object(
        Experiment(code="EXP-003", title="Catalyst C", status="done")
    )
    experiments.write_object(
        Experiment(code="EXP-004", title="Solvent X", status="done")
    )
    return experiments


# ═════════════════════════════════════════════════════════════════════
# §4 — Query DSL: typed predicates, no string SQL
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   "SELECT * FROM x WHERE status = 'done' AND title LIKE 'Cat%'"
#   is fine until you build it from user input. Then it's an injection
#   class, plus mypy can't help you spot a typo in a column name.
# THE FIX (with):
#   `Model.fields.<name>` is a typed accessor; `==`, `>`, `<`, `in_`
#   compose into Conditions; `OrGroup` for disjunctions. Everything
#   parameterized; columns checked against the model.
# ─────────────────────────────────────────────────────────────────────
def query_demo(experiments: Collection) -> None:
    section(4, "Query DSL — typed Field accessors, no string SQL")

    done = experiments.read_objects(Experiment.fields.status == "done")
    assert len(done) == 2, f"expected 2 done experiments, got {len(done)}"
    ok(f"`status == 'done'` returned {len(done)} rows")

    catalysts_or_running = experiments.read_objects(
        (Experiment.fields.title == "Catalyst A")
        | (Experiment.fields.status == "running"),
    )
    assert len(catalysts_or_running) >= 2
    ok(f"OrGroup returned {len(catalysts_or_running)} rows")


# ═════════════════════════════════════════════════════════════════════
# §5 — `renamed_from`: rename a field without writing a migration
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   You realize "duration_s" should have been "elapsed_s". With a
#   classic ORM:  write Alembic migration → backfill → ship → pray.
#   With a JSON-blob store and no help: every read of an old row
#   blows up with KeyError because the JSON still says "duration_s".
# THE FIX (with):
#   `renamed_from("duration_s", default=...)` makes Pydantic accept
#   BOTH the old key (read path) and the new one (write path). Old
#   rows are read transparently; the next save rewrites them with
#   the new key. Zero migration.
# ─────────────────────────────────────────────────────────────────────
def renamed_from_demo(conn: Engine) -> None:
    section(5, "renamed_from — rename without ALTER TABLE")

    # v1: original schema
    class TrialV1(CentauroModel):
        __centauro_name__ = "trial_evo"
        label: str = ""
        duration_s: float = 0.0

    col = Collection(conn, "trials_evo")
    t = TrialV1(label="legacy", duration_s=12.5)
    col.write_object(t)
    legacy_id = t._row.id

    # v2: field renamed. Same persistent name, new model class.
    class TrialV2(CentauroModel):
        __centauro_name__ = "trial_evo"
        label: str = ""
        elapsed_s: float = renamed_from("duration_s", default=0.0)

    # The old row, written under the v1 key, reads cleanly under v2.
    loaded = col.read_object_by_id(legacy_id, TrialV2)
    assert loaded.elapsed_s == 12.5
    ok("v1 row (key='duration_s') read as v2 field 'elapsed_s' = 12.5")


# ═════════════════════════════════════════════════════════════════════
# §6 — `parent_ref`: 1-to-many without FK ceremony
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   Plain JSON-blob stores make you encode the parent id by hand and
#   write your own "find children" query. Real ORMs make you declare
#   FK + back_populates + relationship() + cascade.
# THE FIX (with):
#   `parent_ref(Parent)` registers the link at class-definition time.
#   `write_child(child, parent=…)` sets the FK. `children_of(parent,
#   ChildClass)` returns hydrated children.
# ─────────────────────────────────────────────────────────────────────
def relations_demo(experiments: Collection, conn: Engine) -> None:
    section(6, "parent_ref — 1-to-many without FK boilerplate")

    parent = experiments.read_objects(Experiment.fields.code == "EXP-002")[0]

    trials = Collection(conn, "trials")
    trials.write_child(Trial(label="t1", duration_s=4.2, success=True), parent=parent)
    trials.write_child(Trial(label="t2", duration_s=3.9, success=True), parent=parent)
    trials.write_child(Trial(label="t3", duration_s=5.1, success=False), parent=parent)

    kids = trials.children_of(parent, Trial)
    assert len(kids) == 3
    ok(f"children_of({parent.code}) → {len(kids)} trials")

    succeeded = trials.read_objects(Trial.fields.success == True)  # noqa: E712
    assert len(succeeded) == 2
    ok(f"query on FK-bearing collection works: {len(succeeded)} successful")


# ═════════════════════════════════════════════════════════════════════
# §7 — Views: indexable JSON projections
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   JSON-blob storage is great for writes, miserable for ad-hoc SQL:
#   `SELECT json_extract(meta, '$.title') FROM …` everywhere, no
#   column names, no SQL clients showing real shapes.
# THE FIX (with):
#   `create_view(name, Model)` materializes a SQL VIEW with one
#   column per model field, COALESCE-ing renamed fields so old rows
#   stay queryable. Standard SQL clients see real columns.
# ─────────────────────────────────────────────────────────────────────
def views_demo(experiments: Collection, conn: Engine) -> None:
    section(7, "Views — JSON fields exposed as real SQL columns")

    experiments.create_view("dashboard", Experiment, overwrite=True)
    df = conn.sql_select(
        "SELECT code, title, status FROM experiments_view_dashboard ORDER BY code"
    )
    assert df.height == 4
    ok(f"view 'experiments_view_dashboard' has {df.height} rows, columns={df.columns}")
    print("    " + str(df).replace("\n", "\n    "))


# ═════════════════════════════════════════════════════════════════════
# §8 — Indexes: speed up JSON-path queries
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   JSON-blob queries scan every row's meta. With a few thousand
#   objects you stop noticing; with a few million you pay every read.
# THE FIX (with):
#   `create_index(field)` creates a SQLite expression index on the
#   JSON-extracted path. Transparent to all queries.
# ─────────────────────────────────────────────────────────────────────
def indexes_demo(experiments: Collection) -> None:
    section(8, "Indexes — expression indexes on JSON paths")
    experiments.create_index("status")
    listed = experiments.list_indexes()
    assert any("status" in i for i in listed)
    ok(f"indexes after create_index('status'): {listed}")


# ═════════════════════════════════════════════════════════════════════
# §9 — TimeSeriesCollection: one series per object, Polars-ready
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   Storing time series in a relational schema means a child table,
#   composite PK, your own bulk-insert path, your own dedup logic on
#   timestamp collisions. Plus glue code to get a DataFrame out.
# THE FIX (with):
#   `CentauroModelSeries` carries a `values: list[CentauroValues]`
#   field stored in a dedicated values table. `update_object` accepts
#   `values_mode="append" | "upsert" | "replace"`. `.df` returns a
#   typed Polars DataFrame.
# ─────────────────────────────────────────────────────────────────────
class Sensor(CentauroModelSeries):
    __centauro_name__ = "sensor"
    location: str = ""
    unit: str = "C"


def timeseries_demo(conn: Engine) -> None:
    section(9, "TimeSeriesCollection — built-in series, .df → Polars")
    sensors = TimeSeriesCollection(conn, "sensors")

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    s = Sensor(
        location="lab-A",
        unit="C",
        values=[
            CentauroValues(time=t0 + timedelta(hours=i), value=20.0 + i * 0.1)
            for i in range(24)
        ],
    )
    sensors.write_object(s)
    ok(f"wrote sensor with {len(s.values)} hourly readings")

    # Append a second day with values_mode='append'
    s.values = [
        CentauroValues(time=t0 + timedelta(days=1, hours=i), value=21.0 + i * 0.05)
        for i in range(24)
    ]
    sensors.update_object(s, values_mode="append")

    fresh = sensors.read_object_by_id(s._row.id, Sensor)
    assert len(fresh.values) == 48, f"expected 48, got {len(fresh.values)}"
    ok(f"after append: total readings = {len(fresh.values)}")

    df = fresh.df
    assert df.height == 48 and set(df.columns) == {"time", "value"}
    ok(f"`.df` → Polars DataFrame {df.shape}, schema={df.schema}")


# ═════════════════════════════════════════════════════════════════════
# §10 — `sql_select`: escape hatch for queries the DSL can't express
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   Eventually a stakeholder asks for a percentile, a window function,
#   a CTE — and your DSL bails. The classic ORM answer is "drop down
#   to raw SQL but lose the result-row mapping".
# THE FIX (with):
#   `engine.sql_select(sql, params)` runs SELECT/CTE only (writes are
#   guarded) and returns a Polars DataFrame. Read-only by design.
# ─────────────────────────────────────────────────────────────────────
def sql_select_demo(conn: Engine) -> None:
    section(10, "sql_select — read-only escape hatch → Polars")
    df = conn.sql_select(
        """
        WITH per_status AS (
            SELECT
                json_extract(meta, '$.status') AS status,
                COUNT(*)                       AS n
            FROM experiments_objects
            GROUP BY 1
        )
        SELECT status, n FROM per_status ORDER BY n DESC
        """
    )
    assert df.height >= 2
    ok(f"CTE aggregate via sql_select: {df.height} rows")
    print("    " + str(df).replace("\n", "\n    "))


# ═════════════════════════════════════════════════════════════════════
# §12 — AsyncCollection: await-shaped call sites
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   In FastAPI / aiohttp / anyio code, every blocking call freezes
#   the event loop. You'd write `run_in_executor` wrappers manually
#   for every read/write.
# THE FIX (with):
#   `AsyncCollection` mirrors the sync API with `await`. (On SQLite
#   it's a thread-pool wrapper — correct, but doesn't unlock real
#   parallelism. On Postgres the same caveat applies until 0.6.)
# ─────────────────────────────────────────────────────────────────────
async def async_demo(conn_async: Engine) -> None:
    section(12, "AsyncCollection — same API, awaitable")

    class Note(CentauroModel):
        __centauro_name__ = "note"
        text: str = ""

    col = AsyncCollection(conn_async, "notes")
    n = Note(text="async hello")
    await col.write_object(n)
    loaded = await col.read_object_by_id(n._row.id, Note)
    assert loaded.text == "async hello"
    ok("await write_object / read_object_by_id round-trip OK")


# ═════════════════════════════════════════════════════════════════════
# §11 — Exceptions: catch the cause, not IntegrityError
# ═════════════════════════════════════════════════════════════════════
# THE PAIN (without):
#   "Did this fail because the row doesn't exist, or because I never
#    persisted it, or because the FK was wrong?" — generic
#   IntegrityError makes you parse driver messages.
# THE FIX (with):
#   A small, named hierarchy: ObjectNotFoundError, ObjectNotPersisted-
#   Error, ObjectAlreadyPersistedError, UnlinkedParentError, etc.
#   Each one is the direct answer to a specific class of mistake.
# ─────────────────────────────────────────────────────────────────────
def exceptions_demo(conn: Engine) -> None:
    section(11, "Typed exceptions — diagnose, don't grep error strings")
    col = Collection(conn, "experiments")
    try:
        col.read_object_by_id(999_999, Experiment)
    except ObjectNotFoundError as exc:
        ok(f"caught ObjectNotFoundError as expected: {exc.__class__.__name__}")
    else:
        raise AssertionError("expected ObjectNotFoundError")


# ═════════════════════════════════════════════════════════════════════
# Driver
# ═════════════════════════════════════════════════════════════════════
def main() -> int:
    print("CentauroDB end-to-end SQLite walkthrough")
    print("Python:", sys.version.split()[0])

    conn, db_path = setup_engine()

    section(2, "Models — Pydantic IS the schema (see Experiment/Trial above)")
    step("class Experiment(CentauroModel) — JSON-backed; no migrations on field add")
    step("class Trial(CentauroModel)      — has parent_ref(Experiment); see [6]")

    try:
        experiments = crud_demo(conn)
        query_demo(experiments)
        renamed_from_demo(conn)
        relations_demo(experiments, conn)
        views_demo(experiments, conn)
        indexes_demo(experiments)
        timeseries_demo(conn)
        sql_select_demo(conn)
        exceptions_demo(conn)
    finally:
        conn.close()

    # Async demo uses its own engine with check_same_thread=False, as
    # required by AsyncCollection's thread-pool wrapper.
    async_path = db_path.with_name("lab-async.db")
    conn_async = Engine(f"sqlite:///{async_path}", check_same_thread=False)
    try:
        asyncio.run(async_demo(conn_async))
    finally:
        conn_async.close()

    print("\n" + "=" * 68)
    print(" All sections completed successfully.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
