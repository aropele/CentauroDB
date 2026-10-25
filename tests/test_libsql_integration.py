"""Remote libSQL integration tests.

Skipped by default. Enable with CENTAURODB_LIBSQL_URL and
CENTAURODB_LIBSQL_AUTH_TOKEN.
"""

from __future__ import annotations

import os

import pytest

from centaurodb import CentauroModel, Collection, Engine
from centaurodb.engine import LibSQLEngine

URL = os.environ.get("CENTAURODB_LIBSQL_URL")
TOKEN = os.environ.get("CENTAURODB_LIBSQL_AUTH_TOKEN")
pytestmark = pytest.mark.skipif(
    URL is None or TOKEN is None,
    reason="CENTAURODB_LIBSQL_URL / CENTAURODB_LIBSQL_AUTH_TOKEN not set",
)


class LibSQLThing(CentauroModel):
    __centauro_name__ = "LibSQLThing"
    label: str = ""


@pytest.fixture
def remote():
    c = Engine(URL, auth_token=TOKEN)
    assert isinstance(c, LibSQLEngine)
    try:
        yield c
    finally:
        c.execute("DROP TABLE IF EXISTS libsqltest_objects")
        c.commit()
        c.close()


def test_write_then_read_roundtrip_on_remote_libsql(remote):
    col = Collection(remote, "libsqltest")
    thing = LibSQLThing(label="x")
    col.write_objects([thing])
    rows = col.read_objects(LibSQLThing)
    assert len(rows) == 1
    assert rows[0].row.id is not None
