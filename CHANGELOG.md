# Changelog

All notable changes to CentauroDB are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0 means the public API may still evolve; breaking changes will be called
out explicitly in the release notes below.

## [Unreleased]

## [0.9.0] — 2026-06-10

First public release on PyPI. Beta status: the SQLite path is ready for
real workloads (data pipelines, ingestion, time-series with metadata,
quant backtesting); the PostgreSQL path works on the same API surface
but is production-incomplete on the concurrency axis (no connection
pool yet, async wraps sync psycopg in `to_thread`). Pre-1.0 means the
public API may still evolve — breaking changes will be called out
explicitly here.

### Added

- **Pydantic-native persistence.** `CentauroModel` + `Collection`
  stores model instances as JSON blobs in a fixed five-column shape
  (`id`, `name`, `write_time`, `edit_time`, `meta`). Adding a field
  to a model never requires a migration — just give it a default
  value.
- **Schema evolution without migrations.** `renamed_from=` on a field
  handles the rename-without-rewrite case that ORMs typically push to
  Alembic. Three class-definition-time guardrails (all fields must
  have defaults; `extra='forbid'` rejected; `__centauro_name__`
  required) keep stored data readable across versions.
- **TimeSeriesCollection** with one-line `.df` Polars conversion.
  Internally paired `{name}_objects` + `{name}_values` tables hidden
  behind the public API per ADR-0001.
- **One API, two backends.** SQLite (zero-config, embedded) and
  PostgreSQL (via `centaurodb[postgres]`) share the same `Engine` /
  `Collection` surface; the dialect adapts from the URL.
- **Query DSL.** `Field` + `OrGroup` for typed AND/OR predicates over
  JSON columns; `sql_select()` as a single-collection raw-SQL escape
  hatch returning a Polars DataFrame.
- **Async wrappers.** `AsyncCollection` / `AsyncTimeSeriesCollection`
  for `await`-shaped call sites. See *Known limitations* below.
- **One-to-many relationships** via `parent_ref`, with the parent and
  child rows living in the same collection (single-collection model,
  ADR-0001). `parent_ref()` now accepts an `on_delete` keyword (see
  *Changed*).
- **Views** with idempotent create/replace semantics; **indexes** with
  the same.
- **`parent_ref(on_delete=...)` reservation.** `on_delete="restrict"`
  is the only mode implemented today; `"cascade"` and `"set_null"`
  are accepted as type-checked values but raise
  `NotImplementedError` at delete time. This locks the call shape so
  a future cascade implementation can land without breaking caller
  code.
- **ADR-0001 — Single-collection query model.** Codifies the design
  decision that `read_objects`, `count_objects`, `aggregate`, and
  `children_of` target one collection table per call. Cross-collection
  JOINs are out of scope by design, not deferred.
- **ADR-0002 — Storage format guarantees.** Codifies the
  five-canonical-columns commitment; future system features land in
  `meta._sys.*` or sibling tables.
- **CI matrix runs against real Postgres.** A `postgres:16` service
  container is wired into `.github/workflows/test.yml`, so the 9
  PostgreSQL integration tests run on every push across the Python
  3.11 / 3.12 / 3.13 matrix.

### Changed

- **`parent_ref` default behaviour now refuses delete with children.**
  In the never-released 0.5.0 snapshot, calling `delete_object(parent)`
  silently left orphan child rows. As of 0.9.0, the new default
  (`on_delete="restrict"`) raises `ParentHasChildrenError` instead.
  Callers that need to remove a parent must first call
  `delete_objects(child_filter)`, then the parent delete. This is a
  small backwards-incompatible change vs the 0.5.0 snapshot; it is
  pre-1.0 legal and there is no installed external user base (0.5.0
  was never tagged on PyPI).

### Known limitations

These are tracked for the 0.10+ line, not bugs:

- **No connection pool.** A `PostgresEngine` holds a single
  connection. Fine for batch jobs, ETL workers, pipelines, ingestion;
  not yet sized for high-concurrency web backends.
- **Async wraps sync psycopg in `asyncio.to_thread`.** Correct under
  FastAPI, but it does not unlock native-async concurrency on
  PostgreSQL. A native `psycopg.AsyncConnectionPool` path is on the
  roadmap; the public `AsyncCollection` signature is forward-compatible.
- **No built-in pagination helpers beyond `limit` / `offset` on
  `read_objects`.** `count_objects` returns totals; build your own
  pager on top of those two.

### Notes

- License: MIT.
- Python 3.11+. Tested on Linux, macOS, Windows.
- 591 tests passing on the SQLite path; 9 PostgreSQL integration
  tests additionally run in CI against the `postgres:16` service
  container. ~4,000 LOC across the package.

## [0.5.0] (unreleased internal snapshot, never tagged)

The `[0.5.0]` entry that previously appeared here described an
internal snapshot that was never pushed to PyPI. Its contents are
folded into the 0.9.0 entry above; the section is preserved as a
placeholder so that the chronology in this file matches the public
release history. The only behaviour change relative to that snapshot
is the `parent_ref` default — see *Changed* above.

[Unreleased]: https://github.com/aropele/centaurodb/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/aropele/centaurodb/releases/tag/v0.9.0
