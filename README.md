# CentauroDB

[![CI](https://github.com/aropele/CentauroDB/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/aropele/CentauroDB/actions/workflows/test.yml)
[![PyPI version](https://img.shields.io/pypi/v/centaurodb)](https://pypi.org/project/centaurodb/)
[![Python versions](https://img.shields.io/pypi/pyversions/centaurodb)](https://pypi.org/project/centaurodb/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

**Pydantic-native storage for time-series and evolving data models.**
SQLite + PostgreSQL, schema evolution without migrations, built-in
Polars DataFrames.

## 📖 Documentation

**Everything is at [cdb-nuxt.vercel.app](https://cdb-nuxt.vercel.app/)** —
quickstart, guides, API reference, design notes.

## Install

```bash
pip install centaurodb
```

## Taste

```python
from centaurodb import Engine, Collection, CentauroModel

class Book(CentauroModel):
    __centauro_name__ = "book"
    title: str = ""
    rating: float = 0.0

books = Collection(Engine("library.db"), "library")
books.write_object(Book(title="Dune", rating=4.8))

for book in books.read_objects(Book.fields.rating > 4.5):
    print(book.title, book.rating)
```

Adding a field to a model never requires a migration — give it a
default value and old rows keep loading. Time-series are first-class:
`TimeSeriesCollection` reads back as a Polars DataFrame via `.df`.

[→ Full quickstart and guides](https://cdb-nuxt.vercel.app/)

## License

MIT — see [LICENSE](./LICENSE). Release history in
[CHANGELOG.md](./CHANGELOG.md).
