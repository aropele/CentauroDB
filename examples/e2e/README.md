# End-to-end SQLite walkthrough

A single executable Python file that exercises every public feature of
CentauroDB on SQLite, from a beginner's perspective. It serves three
purposes at once:

1. **Tutorial.** Read `sqlite-project.py` top-to-bottom. Every section
   begins with a `THE PAIN` / `THE FIX` comment block: what you would
   write *without* the feature, and what CentauroDB lets you write
   instead.
2. **Smoke test.** Each section ends with `assert` statements. If the
   library regresses on any feature, the script aborts.
3. **CI gate.** `.github/workflows/e2e.yml` runs this file on every
   push across Python 3.11 / 3.12 / 3.13.

## Running it

```bash
uv run python examples/e2e/sqlite-project.py
```

No environment setup, no database, no fixtures — it creates a temp
SQLite file and tears it down.

## What it covers

```
[1]  Engine                 zero-config SQLite via URL
[2]  Models                 CentauroModel as the only source of truth
[3]  CRUD                   write / read / update / delete on instances
[4]  Query DSL              Field accessors, OrGroup, no string SQL
[5]  renamed_from           field rename without an ALTER TABLE
[6]  parent_ref             1-to-many with write_child / children_of
[7]  Views                  JSON fields exposed as real SQL columns
[8]  Indexes                expression indexes on JSON paths
[9]  TimeSeriesCollection   built-in series + .df → Polars
[10] sql_select             read-only SQL escape hatch → Polars
[11] Typed exceptions       diagnose by class, not error string
[12] AsyncCollection        same API, awaitable
```

## When to update

Any time a new public feature lands in `__all__`, add a section here
with the same `THE PAIN / THE FIX / ASSERT` shape. The walkthrough is
the contract that says "this feature is part of the supported surface".
