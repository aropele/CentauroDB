"""End-to-end persistence: write through one Engine, close, reopen, read.

The rest of the suite leans on ``Engine(":memory:")``, which never
exercises the path that actually matters in production: open a file,
write rows, drop the engine, then open the same file from a fresh
process and read everything back. A regression in WAL flushing,
PRAGMA setup, JSON serialisation, or schema bootstrap can hide
forever in a memory-only suite.

These tests use ``tmp_path`` so each run gets an isolated file and
the runtime never touches the user's filesystem outside pytest's
temp dir.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from centaurodb import (
    CentauroModel,
    CentauroModelSeries,
    CentauroValues,
    Collection,
    Engine,
    TimeSeriesCollection,
)


class Book(CentauroModel):
    __centauro_name__ = "Book"
    title: str = ""
    rating: float = 0.0
    pages: int = 0


class Reading(CentauroModelSeries):
    __centauro_name__ = "Reading"
    sensor: str = ""


def _open(path) -> Engine:
    return Engine(str(path))


class TestCollectionReload:
    def test_rows_survive_engine_close_and_reopen(self, tmp_path):
        db = tmp_path / "library.db"

        with _open(db) as e:
            col = Collection(e, "shelf")
            col.write_objects(
                [
                    Book(title="Dune", rating=4.8, pages=412),
                    Book(title="Foundation", rating=4.6, pages=255),
                    Book(title="Hyperion", rating=4.7, pages=482),
                ]
            )

        with _open(db) as e:
            col = Collection(e, "shelf")
            books = col.read_objects(Book)
            titles = sorted(b.title for b in books)
            assert titles == ["Dune", "Foundation", "Hyperion"]
            assert col.count_objects(Book) == 3

    def test_pagination_after_reload_is_deterministic(self, tmp_path):
        db = tmp_path / "paged.db"

        with _open(db) as e:
            col = Collection(e, "shelf")
            col.write_objects(
                [Book(title=f"B{i:02d}", rating=float(i)) for i in range(25)]
            )

        with _open(db) as e:
            col = Collection(e, "shelf")
            page1 = col.read_objects(Book, limit=10, offset=0)
            page2 = col.read_objects(Book, limit=10, offset=10)
            page3 = col.read_objects(Book, limit=10, offset=20)
            assert len(page1) == 10
            assert len(page2) == 10
            assert len(page3) == 5
            ids = [o._row.id for o in page1 + page2 + page3]
            assert ids == sorted(ids)
            assert len(set(ids)) == 25

    def test_update_then_reload_reflects_change(self, tmp_path):
        db = tmp_path / "updates.db"

        with _open(db) as e:
            col = Collection(e, "shelf")
            book = Book(title="Dune", rating=4.0)
            col.write_object(book)

        with _open(db) as e:
            col = Collection(e, "shelf")
            [book] = col.read_objects(Book.fields.title == "Dune")
            book.rating = 5.0
            col.update_object(book)

        with _open(db) as e:
            col = Collection(e, "shelf")
            [book] = col.read_objects(Book.fields.title == "Dune")
            assert book.rating == 5.0

    def test_delete_then_reload_row_is_gone(self, tmp_path):
        db = tmp_path / "deletes.db"

        with _open(db) as e:
            col = Collection(e, "shelf")
            book = Book(title="Throwaway")
            col.write_object(book)

        with _open(db) as e:
            col = Collection(e, "shelf")
            [book] = col.read_objects(Book.fields.title == "Throwaway")
            col.delete_object(book)

        with _open(db) as e:
            col = Collection(e, "shelf")
            assert col.read_objects(Book) == []
            assert col.count_objects(Book) == 0


class TestTimeSeriesCollectionReload:
    def test_values_survive_engine_close_and_reopen(self, tmp_path):
        db = tmp_path / "ts.db"

        with _open(db) as e:
            col = TimeSeriesCollection(e, "sensors")
            r = Reading(
                sensor="A",
                values=[
                    CentauroValues(time=datetime(2026, 1, 1, 0, 0), value=1.0),
                    CentauroValues(time=datetime(2026, 1, 1, 0, 1), value=2.0),
                    CentauroValues(time=datetime(2026, 1, 1, 0, 2), value=3.0),
                ],
            )
            col.write_object(r)

        with _open(db) as e:
            col = TimeSeriesCollection(e, "sensors")
            [r] = col.read_objects(Reading)
            assert [v.value for v in r.values] == [1.0, 2.0, 3.0]


class TestSchemaSurvivesReload:
    def test_existing_collection_does_not_recreate_or_drop(self, tmp_path):
        db = tmp_path / "schema.db"

        with _open(db) as e:
            col = Collection(e, "shelf")
            col.write_object(Book(title="X"))
            initial_id = col.read_objects(Book)[0]._row.id

        with _open(db) as e:
            col = Collection(e, "shelf")
            col.write_object(Book(title="Y"))
            ids = sorted(o._row.id for o in col.read_objects(Book))
            assert initial_id in ids
            assert len(ids) == 2

    def test_index_persists_across_reload(self, tmp_path):
        db = tmp_path / "indexed.db"

        with _open(db) as e:
            col = Collection(e, "shelf")
            col.write_object(Book(title="X"))
            col.create_index("title")

        with _open(db) as e:
            indexes = e.fetchall("SELECT name FROM sqlite_master WHERE type='index'")
            names = {row["name"] for row in indexes}
            assert any("title" in n for n in names), (
                f"Expected an index containing 'title' to persist, got {names}"
            )


@pytest.mark.parametrize("scheme", ["plain", "sqlite_url"])
def test_url_scheme_roundtrip(tmp_path, scheme):
    db = tmp_path / "scheme.db"
    url = str(db) if scheme == "plain" else f"sqlite:///{db.as_posix()}"

    with Engine(url) as e:
        Collection(e, "shelf").write_object(Book(title="Hello"))

    with Engine(url) as e:
        [book] = Collection(e, "shelf").read_objects(Book)
        assert book.title == "Hello"
