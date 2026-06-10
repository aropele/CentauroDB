# CentauroDB

[![CI](https://github.com/aropele/centaurodb/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/aropele/centaurodb/actions/workflows/test.yml)
[![PyPI version](https://img.shields.io/pypi/v/centaurodb)](https://pypi.org/project/centaurodb/)
[![Python versions](https://img.shields.io/pypi/pyversions/centaurodb)](https://pypi.org/project/centaurodb/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

**Docs:** [centaurodb.dev](https://centaurodb.dev)

**Pydantic-native storage for time-series and evolving data models.**
SQLite + PostgreSQL backends, schema evolution without migrations, built-in
Polars DataFrames.

```bash
pip install centaurodb
```

Optional extras:

```bash
pip install "centaurodb[polars]"     # for .df / sql_select() → DataFrame
pip install "centaurodb[postgres]"   # for PostgreSQL backend
```

---

## Why CentauroDB?

CentauroDB is **not another ORM**. It targets a different problem:

- You define data with **Pydantic models**, not table schemas.
- Models are stored as **JSON blobs**, so adding a field never requires a
  migration — just give it a default value.
- Time-series data is a **first-class citizen** via `TimeSeriesCollection`,
  with one-line conversion to a Polars DataFrame.
- Same API on **SQLite** (zero-config, embedded) and **PostgreSQL**
  (production) — pick the URL, the dialect adapts.

### Who is this for?

Built for **data and analytics pipelines, IoT/sensor ingestion, quant
backtesting, and analytics-shaped backends** — workloads where schemas
keep evolving, where the read path ends in a DataFrame, and where the
write side is a process or a small fleet of workers, not a thousand
concurrent web requests.

| You want | Use |
|---|---|
| Web app with relational schema, FK constraints, complex joins | SQLAlchemy / SQLModel |
| High-concurrency web backend with auth / RLS / connection pooling | Supabase + SQLAlchemy |
| Document store with full-text search and replication | MongoDB |
| **Pydantic models in, structured storage out, DataFrames back** | **CentauroDB** |
| **Time-series + metadata with evolving schemas** | **CentauroDB** |

---

## Quickstart

```python
from centaurodb import Engine, Collection, CentauroModel

class Book(CentauroModel):
    __centauro_name__ = "book"
    title: str = ""
    author: str = ""
    rating: float = 0.0

engine = Engine("library.db")          # or "sqlite://" for in-memory
                                       # or "postgresql://user:pw@host/db"
books = Collection(engine, "library")

# Write
dune = Book(title="Dune", author="Herbert", rating=4.8)
books.write_object(dune)

# Update
dune.rating = 5.0
books.update_object(dune)

# Query (JSON fields, AND/OR conditions)
top = books.read_objects(Book.fields.rating > 4.5)
for b in top:
    print(b.title, b.rating)

# Paginate large result sets
page = books.read_objects(Book.fields.rating > 4.5, limit=20, offset=40)
total = books.count_objects(Book.fields.rating > 4.5)

# Delete
books.delete_object(dune)
```

---

## Time-series with Polars

```python
from datetime import datetime
import polars as pl
from centaurodb import Engine, TimeSeriesCollection, CentauroModelSeries

class StockPrice(CentauroModelSeries):
    __centauro_name__ = "stock"
    ticker: str = ""
    exchange: str = "NYSE"

engine = Engine("stocks.db")
prices = TimeSeriesCollection(engine, "portfolio")

df = pl.DataFrame({
    "time":  [datetime(2026, 1, 1), datetime(2026, 1, 2)],
    "value": [185.20, 187.55],
})

apple = StockPrice(ticker="AAPL", values=df)
prices.write_object(apple)

# Read back, filtered by JSON metadata
[apple] = prices.read_objects(StockPrice.fields.ticker == "AAPL")

# .df is a Polars DataFrame with (time, value)
print(apple.df)
```

If your tool today is `pandas.read_csv` plus a folder of parquet files, this
is the next step from that.

---

## Schema evolution without migrations

Add a field — give it a default. Old rows still load fine:

```python
class Book(CentauroModel):
    __centauro_name__ = "book"
    title: str = ""
    author: str = ""
    rating: float = 0.0
    pages: int | None = None      # NEW — old rows just see None
```

Rename a field — declare the old name as an alias:

```python
from centaurodb import renamed_from

class Book(CentauroModel):
    __centauro_name__ = "book"
    page_count: int = renamed_from("pages", default=0)
```

Three guardrails enforced at class-definition time keep stored data readable
across versions:

1. All fields must have a default value.
2. `extra='forbid'` is rejected (old keys must be silently dropped).
3. `__centauro_name__` is mandatory — it's the stable storage identifier.

---

## Async

`AsyncCollection` and `AsyncTimeSeriesCollection` mirror the sync API and run
under `asyncio`:

```python
from centaurodb import Engine, AsyncCollection

async def main():
    engine = Engine("postgresql://localhost/mydb")
    books = AsyncCollection(engine, "library")
    results = await books.read_objects(Book.fields.rating > 4.5)
```

---

## Status

- 591 tests passing (610 collected; the 14 PostgreSQL integration
  tests skip without a `CENTAURODB_PG_URL` env var and run in CI
  against a real `postgres:16` service container), ~4,000 LOC, fully
  type-hinted (PEP 561 `py.typed`).
- v0.9.0 — beta. Stable storage format (five canonical columns —
  `id`, `name`, `write_time`, `edit_time`, `meta` — committed per
  ADR-0002, storage-format guarantees; future
  system features land in `meta._sys.*` or sibling tables, never as
  new columns) and stable public API on the surfaces marked stable.
- See [CHANGELOG.md](./CHANGELOG.md) for release history.

### Production status & known limits

Honest about what's not in the box yet — so you can decide whether
the gaps matter for your workload:

- **No connection pooling.** A `PostgresEngine` holds a single
  connection. Fine for batch jobs, ETL, ingestion workers, and
  pipelines. Not yet sized for high-concurrency web backends.
- **`AsyncCollection` runs sync `psycopg` inside `asyncio.to_thread`.**
  It works under FastAPI, but it does not unlock native-async
  concurrency. Treat it as ergonomic compatibility, not a perf win.
  A native `psycopg.AsyncConnectionPool` path is on the roadmap.
- **No automatic pagination.** `read_objects` accepts `limit` /
  `offset` (and `count_objects` for totals); use them on any list
  endpoint that could grow unbounded.
- **No row-level security / built-in auth.** This is a storage
  library, not a backend. Enforce auth at the application boundary.

If your use case is **data pipelines, analytics, time-series, IoT,
or quant research**, none of these are blockers. If it's a
high-concurrency multi-tenant web app, reach for SQLAlchemy +
Supabase instead.

## License

MIT — see [LICENSE](./LICENSE).
