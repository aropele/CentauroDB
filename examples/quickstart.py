"""Quickstart example for CentauroDB.

Run with:

    uv run examples/quickstart.py

Requires the ``polars`` extra:

    uv pip install "centaurodb[polars]"
"""

from datetime import datetime

import polars as pl

from centaurodb import (
    CentauroModel,
    CentauroModelSeries,
    Collection,
    Engine,
    TimeSeriesCollection,
)


class Book(CentauroModel):
    __centauro_name__ = "book"
    title: str = ""
    author: str = ""
    rating: float = 0.0


class StockPrice(CentauroModelSeries):
    __centauro_name__ = "stock"
    ticker: str = ""
    exchange: str = "NYSE"


def basic_crud() -> None:
    engine = Engine("sqlite://")  # in-memory
    books = Collection(engine, "library")

    books.write_object(Book(title="Dune", author="Herbert", rating=4.8))
    books.write_object(Book(title="Foundation", author="Asimov", rating=4.6))
    books.write_object(Book(title="The Pragmatic Programmer", rating=4.2))

    top = books.read_objects(Book.fields.rating > 4.5)
    print("Top-rated books:")
    for b in top:
        print(f"  {b.title} ({b.rating})")


def time_series() -> None:
    engine = Engine("sqlite://")
    prices = TimeSeriesCollection(engine, "portfolio")

    df = pl.DataFrame(
        {
            "time": [datetime(2026, 1, 1), datetime(2026, 1, 2)],
            "value": [185.20, 187.55],
        }
    )
    prices.write_object(StockPrice(ticker="AAPL", values=df))

    [apple] = prices.read_objects(StockPrice.fields.ticker == "AAPL")
    print("\nAAPL price series:")
    for row in apple.df.iter_rows(named=True):
        print(f"  {row['time']}  {row['value']}")


if __name__ == "__main__":
    basic_crud()
    time_series()
