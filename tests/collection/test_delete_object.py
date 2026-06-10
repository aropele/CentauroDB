import pytest

from centaurodb import (
    Collection,
    Engine,
    CentauroModel,
    CentauroModelSeries,
    CentauroValues,
    ObjectNotFoundError,
    ObjectNotPersistedError,
    TimeSeriesCollection,
    AsyncCollection,
    AsyncTimeSeriesCollection,
)


class Book(CentauroModel):
    __centauro_name__ = "Book"
    title: str = ""
    genre: str = ""


class Author(CentauroModel):
    __centauro_name__ = "Author"
    name: str = ""


class Metric(CentauroModelSeries):
    __centauro_name__ = "Metric"
    unit: str = ""


class TestDeleteObject:
    def test_delete_then_read_raises(self, conn):
        col = Collection(conn, "lib")
        book = Book(title="Dune", genre="sci-fi")
        col.write_object(book)
        row_id = book._row.id

        col.delete_object(book)

        with pytest.raises(ObjectNotFoundError):
            col.read_object_by_id(row_id, Book)

    def test_delete_resets_persistence_state(self, conn):
        col = Collection(conn, "lib")
        book = Book(title="Dune")
        col.write_object(book)

        col.delete_object(book)

        assert book._persisted is False
        assert book._row is None

    def test_delete_non_persisted_raises(self, conn):
        col = Collection(conn, "lib")
        book = Book(title="Dune")

        with pytest.raises(ObjectNotPersistedError):
            col.delete_object(book)

    def test_delete_with_none_row_raises(self, conn):
        col = Collection(conn, "lib")
        book = Book(title="Dune")
        book._persisted = True
        book._row = None

        with pytest.raises(ObjectNotPersistedError):
            col.delete_object(book)

    def test_delete_non_centauro_model_raises(self, conn):
        col = Collection(conn, "lib")

        with pytest.raises(TypeError, match="Expected a CentauroModel"):
            col.delete_object("not a model")

    def test_deleted_object_can_be_rewritten(self, conn):
        col = Collection(conn, "lib")
        book = Book(title="Dune")
        col.write_object(book)
        col.delete_object(book)

        col.write_object(book)
        assert book._persisted is True
        assert book._row is not None


class TestDeleteObjects:
    def test_delete_all_by_model_class(self, conn):
        col = Collection(conn, "lib")
        col.write_object(Book(title="Dune"))
        col.write_object(Book(title="Foundation"))

        count = col.delete_objects(Book)

        assert count == 2
        assert col.read_objects(Book) == []

    def test_delete_with_single_condition(self, conn):
        col = Collection(conn, "lib")
        col.write_object(Book(title="Dune", genre="sci-fi"))
        col.write_object(Book(title="Hamlet", genre="drama"))

        count = col.delete_objects(Book.fields.genre == "sci-fi")

        assert count == 1
        remaining = col.read_objects(Book)
        assert len(remaining) == 1
        assert remaining[0].title == "Hamlet"

    def test_delete_with_multiple_conditions(self, conn):
        col = Collection(conn, "lib")
        col.write_object(Book(title="Dune", genre="sci-fi"))
        col.write_object(Book(title="Foundation", genre="sci-fi"))
        col.write_object(Book(title="Hamlet", genre="drama"))

        count = col.delete_objects(
            Book.fields.genre == "sci-fi",
            Book.fields.title == "Dune",
        )

        assert count == 1
        assert len(col.read_objects(Book)) == 2

    def test_delete_with_or_group(self, conn):
        col = Collection(conn, "lib")
        col.write_object(Book(title="Dune", genre="sci-fi"))
        col.write_object(Book(title="Hamlet", genre="drama"))
        col.write_object(Book(title="Cooking 101", genre="cooking"))

        count = col.delete_objects(
            (Book.fields.genre == "sci-fi") | (Book.fields.genre == "drama")
        )

        assert count == 2
        remaining = col.read_objects(Book)
        assert len(remaining) == 1
        assert remaining[0].genre == "cooking"

    def test_delete_returns_zero_when_no_matches(self, conn):
        col = Collection(conn, "lib")
        col.write_object(Book(title="Dune", genre="sci-fi"))

        count = col.delete_objects(Book.fields.genre == "romance")

        assert count == 0
        assert len(col.read_objects(Book)) == 1

    def test_delete_does_not_affect_other_types(self, conn):
        col = Collection(conn, "lib")
        col.write_object(Book(title="Dune"))
        col.write_object(Author(name="Herbert"))

        count = col.delete_objects(Book)

        assert count == 1
        authors = col.read_objects(Author)
        assert len(authors) == 1
        assert authors[0].name == "Herbert"

    def test_delete_unknown_field_raises(self, conn):
        col = Collection(conn, "lib")

        with pytest.raises(AttributeError, match="not found"):
            col.delete_objects(Book.fields.nonexistent == "x")

    def test_delete_with_model_class_and_conditions(self, conn):
        col = Collection(conn, "lib")
        col.write_object(Book(title="Dune", genre="sci-fi"))
        col.write_object(Book(title="Hamlet", genre="drama"))

        count = col.delete_objects(Book, Book.fields.genre == "drama")

        assert count == 1
        remaining = col.read_objects(Book)
        assert len(remaining) == 1
        assert remaining[0].genre == "sci-fi"


class TestDeleteObjectTimeSeries:
    def test_delete_cascades_to_values(self, conn):
        col = TimeSeriesCollection(conn, "metrics")
        m = Metric(
            unit="kg",
            values=[
                CentauroValues(time="2024-01-01", value=1.0),
                CentauroValues(time="2024-01-02", value=2.0),
            ],
        )
        col.write_object(m)
        row_id = m._row.id

        col.delete_object(m)

        # Verify values were cascade-deleted
        rows = conn.fetchall(
            "SELECT * FROM metrics_values WHERE data_object_id = ?", (row_id,)
        )
        assert rows == []

    def test_delete_objects_with_conditions_on_timeseries(self, conn):
        col = TimeSeriesCollection(conn, "metrics")
        col.write_object(
            Metric(
                unit="kg",
                values=[CentauroValues(time="2024-01-01", value=1.0)],
            )
        )
        col.write_object(
            Metric(
                unit="m",
                values=[CentauroValues(time="2024-01-01", value=5.0)],
            )
        )

        count = col.delete_objects(Metric.fields.unit == "kg")

        assert count == 1
        remaining = col.read_objects(Metric)
        assert len(remaining) == 1
        assert remaining[0].unit == "m"


@pytest.mark.asyncio
class TestDeleteObjectAsync:
    async def test_async_delete_object(self):
        with Engine(":memory:", check_same_thread=False) as conn:
            col = AsyncCollection(conn, "lib")
            book = Book(title="Dune")
            await col.write_object(book)
            row_id = book._row.id

            await col.delete_object(book)

            assert book._persisted is False
            with pytest.raises(ObjectNotFoundError):
                await col.read_object_by_id(row_id, Book)

    async def test_async_delete_objects(self):
        with Engine(":memory:", check_same_thread=False) as conn:
            col = AsyncCollection(conn, "lib")
            await col.write_object(Book(title="Dune", genre="sci-fi"))
            await col.write_object(Book(title="Hamlet", genre="drama"))

            count = await col.delete_objects(Book.fields.genre == "sci-fi")

            assert count == 1
            remaining = await col.read_objects(Book)
            assert len(remaining) == 1

    async def test_async_timeseries_delete_object(self):
        with Engine(":memory:", check_same_thread=False) as conn:
            col = AsyncTimeSeriesCollection(conn, "metrics")
            m = Metric(
                unit="kg",
                values=[CentauroValues(time="2024-01-01", value=1.0)],
            )
            await col.write_object(m)

            await col.delete_object(m)

            assert m._persisted is False

    async def test_async_timeseries_delete_objects(self):
        with Engine(":memory:", check_same_thread=False) as conn:
            col = AsyncTimeSeriesCollection(conn, "metrics")
            await col.write_object(
                Metric(
                    unit="kg",
                    values=[CentauroValues(time="2024-01-01", value=1.0)],
                )
            )
            await col.write_object(
                Metric(
                    unit="m",
                    values=[CentauroValues(time="2024-01-01", value=5.0)],
                )
            )

            count = await col.delete_objects(Metric)

            assert count == 2
