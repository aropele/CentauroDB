import pytest
from pydantic import BaseModel

from centaurodb import Engine, CentauroModel, CentauroModelSeries


class SampleModel(CentauroModel):
    __centauro_name__ = "SampleModel"
    unit: str = ""
    location: str = ""


class PlainModel(BaseModel):
    unit: str


class Sensor(CentauroModel):
    __centauro_name__ = "Sensor"
    unit: str = ""
    status: str = "active"
    location: str = ""
    level: float = 0.0


class NamedModel(CentauroModel):
    __centauro_name__ = "CustomName"
    value: float = 0.0


class NamedModelSeries(CentauroModelSeries):
    __centauro_name__ = "CustomSeries"
    unit: str = ""


class SampleModelSeries(CentauroModelSeries):
    __centauro_name__ = "SampleModelSeries"
    unit: str = ""


@pytest.fixture(params=["sqlite", "libsql"])
def conn(request, tmp_path):
    if request.param == "sqlite":
        with Engine() as c:
            yield c
    else:
        pytest.importorskip("libsql")
        from centaurodb.engine import LibSQLEngine

        with LibSQLEngine(str(tmp_path / "conn.db")) as c:
            yield c


def get_tables(conn: Engine) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return [r[0] for r in rows]


def get_column_names(conn: Engine, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def get_column_types(conn: Engine, table: str) -> dict[str, str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"]: r["type"] for r in rows}
