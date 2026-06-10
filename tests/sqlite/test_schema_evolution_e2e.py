"""End-to-end schema evolution: write v1, redeploy v2, read v1 rows.

The renamed_from / "no migrations" story is the core USP, but the
existing tests for it work in isolation on a single class — they
don't exercise the production path where a service is shipped with
schema v1, writes data, gets redeployed with schema v2, and has to
keep reading the v1 rows correctly.

These tests simulate that handover by writing through one class
definition, closing the engine, and re-opening the same file with
a new class definition that shares ``__centauro_name__`` but has
fields renamed, added, or removed.
"""

from __future__ import annotations

import pytest

from centaurodb import (
    CentauroModel,
    Collection,
    Engine,
    renamed_from,
)


def _open(path) -> Engine:
    return Engine(str(path))


class TestRenamedFieldRoundtrip:
    def test_v1_rows_hydrate_under_v2_with_renamed_field(self, tmp_path):
        db = tmp_path / "evo.db"

        # v1: field is named "pg"
        class BookV1(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pg: int = 0

        with _open(db) as e:
            col = Collection(e, "library")
            col.write_objects(
                [
                    BookV1(title="Dune", pg=412),
                    BookV1(title="Foundation", pg=255),
                ]
            )

        # v2: same persistent name, "pg" is now "pages"
        class BookV2(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pages: int = renamed_from("pg", default=0)

        with _open(db) as e:
            col = Collection(e, "library")
            books = sorted(col.read_objects(BookV2), key=lambda b: b.title)
            assert [b.title for b in books] == ["Dune", "Foundation"]
            assert [b.pages for b in books] == [412, 255]

    def test_writing_v2_rows_uses_new_key_in_storage(self, tmp_path):
        db = tmp_path / "evo.db"

        class BookV1(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pg: int = 0

        class BookV2(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pages: int = renamed_from("pg", default=0)

        with _open(db) as e:
            Collection(e, "library").write_object(BookV1(title="OldRow", pg=100))

        with _open(db) as e:
            col = Collection(e, "library")
            col.write_object(BookV2(title="NewRow", pages=200))
            raw_rows = e.fetchall("SELECT meta FROM library_objects ORDER BY id")
            metas = [r["meta"] for r in raw_rows]
            assert '"pg":100' in metas[0] or '"pg": 100' in metas[0]
            assert '"pages":200' in metas[1] or '"pages": 200' in metas[1]

    def test_mixed_v1_and_v2_rows_all_hydrate(self, tmp_path):
        db = tmp_path / "mixed.db"

        class BookV1(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pg: int = 0

        class BookV2(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pages: int = renamed_from("pg", default=0)

        with _open(db) as e:
            Collection(e, "library").write_object(BookV1(title="V1", pg=111))

        with _open(db) as e:
            col = Collection(e, "library")
            col.write_object(BookV2(title="V2", pages=222))
            books = sorted(col.read_objects(BookV2), key=lambda b: b.title)
            assert [(b.title, b.pages) for b in books] == [
                ("V1", 111),
                ("V2", 222),
            ]


class TestAddedField:
    def test_v1_rows_get_default_for_field_added_in_v2(self, tmp_path):
        db = tmp_path / "added.db"

        class BookV1(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""

        with _open(db) as e:
            Collection(e, "library").write_object(BookV1(title="Old"))

        class BookV2(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            rating: float = 3.5  # NEW

        with _open(db) as e:
            col = Collection(e, "library")
            [book] = col.read_objects(BookV2)
            assert book.title == "Old"
            assert book.rating == 3.5  # default applied to legacy row

    def test_query_on_new_field_against_legacy_rows_uses_default_semantics(
        self, tmp_path
    ):
        db = tmp_path / "added_query.db"

        class BookV1(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""

        with _open(db) as e:
            Collection(e, "library").write_objects(
                [BookV1(title="A"), BookV1(title="B")]
            )

        class BookV2(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            rating: float = 0.0

        with _open(db) as e:
            col = Collection(e, "library")
            col.write_object(BookV2(title="C", rating=5.0))
            top = col.read_objects(BookV2.fields.rating > 3.0)
            assert [b.title for b in top] == ["C"]
            all_books = col.read_objects(BookV2)
            assert sorted(b.title for b in all_books) == ["A", "B", "C"]


class TestRemovedField:
    def test_v1_rows_with_removed_field_still_hydrate(self, tmp_path):
        """When a field is removed in v2, old rows still have the
        key in their JSON blob. Hydration must drop it silently
        — that's why CentauroModel rejects extra='forbid'."""
        db = tmp_path / "removed.db"

        class BookV1(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            isbn: str = ""
            pages: int = 0

        with _open(db) as e:
            Collection(e, "library").write_object(
                BookV1(title="Dune", isbn="978-0441172719", pages=412)
            )

        # v2: dropped isbn
        class BookV2(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pages: int = 0

        with _open(db) as e:
            col = Collection(e, "library")
            [book] = col.read_objects(BookV2)
            assert book.title == "Dune"
            assert book.pages == 412
            assert not hasattr(book, "isbn")


class TestThreeVersionEvolution:
    def test_v1_v2_v3_rows_all_readable_under_v3(self, tmp_path):
        """Multi-rename path — every persisted row across three
        deploys must hydrate cleanly under the latest schema."""
        db = tmp_path / "three.db"

        class BookV1(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pg: int = 0  # original name

        with _open(db) as e:
            Collection(e, "library").write_object(BookV1(title="V1", pg=10))

        class BookV2(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            pages: int = renamed_from("pg", default=0)  # renamed once

        with _open(db) as e:
            Collection(e, "library").write_object(BookV2(title="V2", pages=20))

        class BookV3(CentauroModel):
            __centauro_name__ = "Book"
            title: str = ""
            page_count: int = renamed_from("pg", "pages", default=0)  # renamed twice

        with _open(db) as e:
            Collection(e, "library").write_object(BookV3(title="V3", page_count=30))

        with _open(db) as e:
            col = Collection(e, "library")
            books = sorted(col.read_objects(BookV3), key=lambda b: b.title)
            assert [(b.title, b.page_count) for b in books] == [
                ("V1", 10),
                ("V2", 20),
                ("V3", 30),
            ]


@pytest.mark.parametrize(
    "v1_value,v2_default,expected",
    [
        (None, "x", "x"),
        ("preset", "x", "preset"),
    ],
)
def test_added_field_default_resolution(tmp_path, v1_value, v2_default, expected):
    db = tmp_path / f"added_{expected}.db"

    class BookV1(CentauroModel):
        __centauro_name__ = "Book"
        title: str = ""

    class BookV1WithExtra(CentauroModel):
        __centauro_name__ = "Book"
        title: str = ""
        author: str = ""

    with _open(db) as e:
        col = Collection(e, "library")
        if v1_value is None:
            col.write_object(BookV1(title="t"))
        else:
            col.write_object(BookV1WithExtra(title="t", author=v1_value))

    class BookV2(CentauroModel):
        __centauro_name__ = "Book"
        title: str = ""
        author: str = v2_default

    with _open(db) as e:
        [book] = Collection(e, "library").read_objects(BookV2)
        assert book.author == expected
