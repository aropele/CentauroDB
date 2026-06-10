import pytest

from centaurodb import (
    CentauroModel,
    Collection,
    Engine,
    ObjectNotPersistedError,
    ParentHasChildrenError,
    UnlinkedParentError,
    parent_ref,
)


# --- Test models ---


class Portfolio(CentauroModel):
    __centauro_name__ = "Portfolio"
    strategy: str = ""


class Broker(CentauroModel):
    __centauro_name__ = "Broker"
    name: str = ""


class Trade(CentauroModel):
    __centauro_name__ = "Trade"
    portfolio: int | None = parent_ref(Portfolio)
    symbol: str = ""
    quantity: float = 0.0


class MultiParentTrade(CentauroModel):
    __centauro_name__ = "MultiParentTrade"
    portfolio: int | None = parent_ref(Portfolio)
    broker: int | None = parent_ref(Broker)
    symbol: str = ""


class SpecialTrade(Trade):
    __centauro_name__ = "SpecialTrade"
    note: str = ""


class NoRefModel(CentauroModel):
    __centauro_name__ = "NoRefModel"
    value: float = 0.0


@pytest.fixture
def conn():
    with Engine() as c:
        yield c


class TestParentRef:
    def test_parent_ref_field_defaults_to_none(self):
        trade = Trade(symbol="AAPL")
        assert trade.portfolio is None

    def test_parent_ref_registered_in_centauro_refs(self):
        assert "portfolio" in Trade.__centauro_refs__
        assert Trade.__centauro_refs__["portfolio"] is Portfolio

    def test_multi_parent_refs_registered(self):
        refs = MultiParentTrade.__centauro_refs__
        assert refs["portfolio"] is Portfolio
        assert refs["broker"] is Broker

    def test_no_ref_model_has_empty_refs(self):
        assert NoRefModel.__centauro_refs__ == {}

    def test_parent_ref_rejects_non_centauro_model(self):
        with pytest.raises(TypeError, match="CentauroModel subclass"):
            parent_ref(str)  # type: ignore

    def test_parent_ref_accepts_field_kwargs(self):
        field = parent_ref(Portfolio, description="FK to portfolio")
        assert field.description == "FK to portfolio"


class TestWriteTimeValidation:
    def test_write_unlinked_child_raises(self, conn):
        col = Collection(conn, "items")
        trade = Trade(symbol="AAPL")
        with pytest.raises(UnlinkedParentError, match="portfolio"):
            col.write_object(trade)

    def test_write_linked_child_succeeds(self, conn):
        col = Collection(conn, "items")
        portfolio = Portfolio(strategy="momentum")
        col.write_object(portfolio)

        trade = Trade(symbol="AAPL", portfolio=portfolio.row.id)
        col.write_object(trade)
        assert trade._persisted

    def test_write_unlinked_multi_parent_raises_first(self, conn):
        col = Collection(conn, "items")
        trade = MultiParentTrade(symbol="AAPL")
        with pytest.raises(UnlinkedParentError, match="portfolio"):
            col.write_object(trade)

    def test_write_partially_linked_multi_parent_raises(self, conn):
        col = Collection(conn, "items")
        portfolio = Portfolio(strategy="momentum")
        col.write_object(portfolio)

        trade = MultiParentTrade(symbol="AAPL", portfolio=portfolio.row.id)
        with pytest.raises(UnlinkedParentError, match="broker"):
            col.write_object(trade)

    def test_no_ref_model_writes_normally(self, conn):
        col = Collection(conn, "items")
        obj = NoRefModel(value=42.0)
        col.write_object(obj)
        assert obj._persisted


class TestLink:
    def test_link_sets_parent_id(self, conn):
        col = Collection(conn, "items")
        portfolio = Portfolio(strategy="momentum")
        col.write_object(portfolio)

        trade = Trade(symbol="AAPL")
        col._link(trade, to=portfolio)
        assert trade.portfolio == portfolio.row.id

    def test_link_unpersisted_parent_raises(self, conn):
        col = Collection(conn, "items")
        portfolio = Portfolio(strategy="momentum")
        trade = Trade(symbol="AAPL")
        with pytest.raises(ObjectNotPersistedError):
            col._link(trade, to=portfolio)

    def test_link_no_matching_ref_raises(self, conn):
        col = Collection(conn, "items")
        broker = Broker(name="IB")
        col.write_object(broker)

        trade = Trade(symbol="AAPL")
        with pytest.raises(TypeError, match="no parent_ref"):
            col._link(trade, to=broker)

    def test_link_multi_parent_portfolio(self, conn):
        col = Collection(conn, "items")
        portfolio = Portfolio(strategy="momentum")
        col.write_object(portfolio)

        trade = MultiParentTrade(symbol="AAPL")
        col._link(trade, to=portfolio)
        assert trade.portfolio == portfolio.row.id
        assert trade.broker is None

    def test_link_multi_parent_broker(self, conn):
        col = Collection(conn, "items")
        broker = Broker(name="IB")
        col.write_object(broker)

        trade = MultiParentTrade(symbol="AAPL")
        col._link(trade, to=broker)
        assert trade.broker == broker.row.id
        assert trade.portfolio is None


class TestWriteChild:
    def test_write_child_links_and_persists(self, conn):
        col = Collection(conn, "items")
        portfolio = Portfolio(strategy="momentum")
        col.write_object(portfolio)

        trade = Trade(symbol="AAPL", quantity=100)
        col.write_child(trade, parent=portfolio)

        assert trade.portfolio == portfolio.row.id
        assert trade._persisted
        assert trade.row.id is not None

    def test_write_child_roundtrip(self, conn):
        col = Collection(conn, "items")
        portfolio = Portfolio(strategy="value")
        col.write_object(portfolio)

        trade = Trade(symbol="GOOG", quantity=50)
        col.write_child(trade, parent=portfolio)

        loaded = col.read_object_by_id(trade.row.id, Trade)
        assert loaded.portfolio == portfolio.row.id
        assert loaded.symbol == "GOOG"
        assert loaded.quantity == 50

    def test_write_child_unpersisted_parent_raises(self, conn):
        col = Collection(conn, "items")
        portfolio = Portfolio(strategy="momentum")
        trade = Trade(symbol="AAPL")
        with pytest.raises(ObjectNotPersistedError):
            col.write_child(trade, parent=portfolio)


class TestChildrenOf:
    def test_children_of_returns_linked_objects(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t1 = Trade(symbol="AAPL", quantity=100)
        t2 = Trade(symbol="GOOG", quantity=50)
        col.write_child(t1, parent=p)
        col.write_child(t2, parent=p)

        children = col.children_of(p, Trade)
        assert len(children) == 2
        symbols = {c.symbol for c in children}
        assert symbols == {"AAPL", "GOOG"}

    def test_children_of_excludes_other_parents(self, conn):
        col = Collection(conn, "items")
        p1 = Portfolio(strategy="momentum")
        p2 = Portfolio(strategy="value")
        col.write_object(p1)
        col.write_object(p2)

        t1 = Trade(symbol="AAPL")
        t2 = Trade(symbol="GOOG")
        col.write_child(t1, parent=p1)
        col.write_child(t2, parent=p2)

        children_p1 = col.children_of(p1, Trade)
        assert len(children_p1) == 1
        assert children_p1[0].symbol == "AAPL"

        children_p2 = col.children_of(p2, Trade)
        assert len(children_p2) == 1
        assert children_p2[0].symbol == "GOOG"

    def test_children_of_empty_result(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        children = col.children_of(p, Trade)
        assert children == []

    def test_children_of_unpersisted_parent_raises(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        with pytest.raises(ObjectNotPersistedError):
            col.children_of(p, Trade)

    def test_children_of_no_ref_raises(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)
        with pytest.raises(TypeError, match="no parent_ref"):
            col.children_of(p, NoRefModel)

    def test_children_of_raw(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t = Trade(symbol="AAPL")
        col.write_child(t, parent=p)

        rows = col.children_of(p, Trade, raw=True)
        assert len(rows) == 1
        assert "meta" in dict(rows[0])


class TestUpdateObjectValidation:
    def test_update_nullified_parent_ref_raises(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t = Trade(symbol="AAPL")
        col.write_child(t, parent=p)

        t.portfolio = None
        with pytest.raises(UnlinkedParentError, match="portfolio"):
            col.update_object(t)

    def test_update_with_valid_parent_ref_succeeds(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t = Trade(symbol="AAPL")
        col.write_child(t, parent=p)

        t.symbol = "GOOG"
        col.update_object(t)

        loaded = col.read_object_by_id(t.row.id, Trade)
        assert loaded.symbol == "GOOG"
        assert loaded.portfolio == p.row.id


class TestInheritedParentRef:
    def test_subclass_inherits_parent_ref(self):
        assert "portfolio" in SpecialTrade.__centauro_refs__
        assert SpecialTrade.__centauro_refs__["portfolio"] is Portfolio

    def test_write_child_with_inherited_ref(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t = SpecialTrade(symbol="AAPL", note="test")
        col.write_child(t, parent=p)
        assert t.portfolio == p.row.id
        assert t._persisted

    def test_children_of_with_inherited_ref(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t = SpecialTrade(symbol="AAPL", note="urgent")
        col.write_child(t, parent=p)

        children = col.children_of(p, SpecialTrade)
        assert len(children) == 1
        assert children[0].note == "urgent"


class TestConstructWithParentObject:
    def test_pass_persisted_parent_at_construction(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        trade = Trade(symbol="AAPL", quantity=100, portfolio=p)
        assert trade.portfolio == p.row.id

        col.write_object(trade)
        assert trade._persisted

    def test_pass_unpersisted_parent_raises(self):
        p = Portfolio(strategy="momentum")
        with pytest.raises(Exception, match="unpersisted"):
            Trade(symbol="AAPL", portfolio=p)

    def test_roundtrip_with_parent_object(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="value")
        col.write_object(p)

        trade = Trade(symbol="GOOG", quantity=50, portfolio=p)
        col.write_object(trade)

        loaded = col.read_object_by_id(trade.row.id, Trade)
        assert loaded.portfolio == p.row.id
        assert loaded.symbol == "GOOG"

    def test_children_of_after_object_construction(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t1 = Trade(symbol="AAPL", portfolio=p)
        t2 = Trade(symbol="GOOG", portfolio=p)
        col.write_object(t1)
        col.write_object(t2)

        children = col.children_of(p, Trade)
        assert len(children) == 2

    def test_multi_parent_both_resolved(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        b = Broker(name="IB")
        col.write_object(p)
        col.write_object(b)

        trade = MultiParentTrade(symbol="AAPL", portfolio=p, broker=b)
        assert trade.portfolio == p.row.id
        assert trade.broker == b.row.id
        col.write_object(trade)
        assert trade._persisted

    def test_int_still_accepted(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        trade = Trade(symbol="AAPL", portfolio=p.row.id)
        assert trade.portfolio == p.row.id


class TestAssignParentObject:
    def test_assign_persisted_parent_resolves(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        trade = Trade(symbol="AAPL")
        trade.portfolio = p
        assert trade.portfolio == p.row.id

        col.write_object(trade)
        assert trade._persisted

    def test_assign_unpersisted_parent_raises(self):
        p = Portfolio(strategy="momentum")
        trade = Trade(symbol="AAPL")
        with pytest.raises(ValueError, match="unpersisted"):
            trade.portfolio = p

    def test_assign_int_still_works(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        trade = Trade(symbol="AAPL")
        trade.portfolio = p.row.id
        assert trade.portfolio == p.row.id

    def test_assign_none_still_works(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        trade = Trade(symbol="AAPL", portfolio=p)
        trade.portfolio = None
        assert trade.portfolio is None


class TestRefreshWithParentRef:
    def test_refresh_preserves_parent_ref(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t = Trade(symbol="AAPL", portfolio=p)
        col.write_object(t)

        t.symbol = "CHANGED"
        col.refresh(t)
        assert t.symbol == "AAPL"
        assert t.portfolio == p.row.id


class TestQueryByParentRef:
    def test_read_objects_by_query_on_parent_ref(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t1 = Trade(symbol="AAPL", portfolio=p)
        t2 = Trade(symbol="GOOG", portfolio=p)
        col.write_object(t1)
        col.write_object(t2)

        results = col.read_objects(Trade.fields.portfolio == p.row.id)
        assert len(results) == 2
        assert {r.symbol for r in results} == {"AAPL", "GOOG"}

    def test_query_combined_with_other_conditions(self, conn):
        col = Collection(conn, "items")
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t1 = Trade(symbol="AAPL", quantity=100, portfolio=p)
        t2 = Trade(symbol="GOOG", quantity=50, portfolio=p)
        col.write_object(t1)
        col.write_object(t2)

        results = col.read_objects(
            Trade.fields.portfolio == p.row.id,
            Trade.fields.quantity > 75,
        )
        assert len(results) == 1
        assert results[0].symbol == "AAPL"


class TestJsonSchemaCompat:
    def test_model_json_schema_does_not_crash(self):
        schema = Trade.model_json_schema()
        assert "portfolio" in schema["properties"]


class TestViewAndIndexCompat:
    def test_create_view_with_parent_ref_field(self, conn):
        col = Collection(conn, "items")
        col.create_view("trades", Trade, include_fields=["portfolio", "symbol"])
        p = Portfolio(strategy="momentum")
        col.write_object(p)

        t = Trade(symbol="AAPL")
        col.write_child(t, parent=p)

        row = conn.fetchone(
            f"SELECT * FROM {col.name}_view_trades WHERE portfolio = ?",
            (p.row.id,),
        )
        assert row is not None
        assert row["symbol"] == "AAPL"

    def test_create_index_on_parent_ref_field(self, conn):
        col = Collection(conn, "items")
        col.create_index("portfolio")
        assert "portfolio" in col.list_indexes()


class TestCrossCollection:
    def test_parent_in_different_collection(self, conn):
        portfolios = Collection(conn, "portfolios")
        trades = Collection(conn, "trades")

        p = Portfolio(strategy="momentum")
        portfolios.write_object(p)

        t = Trade(symbol="AAPL")
        trades.write_child(t, parent=p)
        assert t.portfolio == p.row.id

        children = trades.children_of(p, Trade)
        assert len(children) == 1
        assert children[0].symbol == "AAPL"


# --- on_delete reservation tests (Chunk 2 / ADR-0001) ---


class RestrictParent(CentauroModel):
    __centauro_name__ = "RestrictParent"
    label: str = ""


class RestrictChild(CentauroModel):
    __centauro_name__ = "RestrictChild"
    parent: int | None = parent_ref(RestrictParent)  # default on_delete='restrict'
    tag: str = ""


class ExplicitRestrictParent(CentauroModel):
    __centauro_name__ = "ExplicitRestrictParent"
    label: str = ""


class ExplicitRestrictChild(CentauroModel):
    __centauro_name__ = "ExplicitRestrictChild"
    parent: int | None = parent_ref(ExplicitRestrictParent, on_delete="restrict")
    tag: str = ""


class CascadeParent(CentauroModel):
    __centauro_name__ = "CascadeParent"
    label: str = ""


class CascadeChild(CentauroModel):
    __centauro_name__ = "CascadeChild"
    parent: int | None = parent_ref(CascadeParent, on_delete="cascade")
    tag: str = ""


class SetNullParent(CentauroModel):
    __centauro_name__ = "SetNullParent"
    label: str = ""


class SetNullChild(CentauroModel):
    __centauro_name__ = "SetNullChild"
    parent: int | None = parent_ref(SetNullParent, on_delete="set_null")
    tag: str = ""


class TestParentRefOnDeleteMarker:
    """Marker-level coverage of the new on_delete kwarg."""

    def test_default_is_restrict(self):
        marker = RestrictChild.__centauro_ref_markers__["parent"]
        assert marker.on_delete == "restrict"

    def test_explicit_restrict_matches_default(self):
        marker = ExplicitRestrictChild.__centauro_ref_markers__["parent"]
        assert marker.on_delete == "restrict"

    def test_cascade_stored_on_marker(self):
        marker = CascadeChild.__centauro_ref_markers__["parent"]
        assert marker.on_delete == "cascade"

    def test_set_null_stored_on_marker(self):
        marker = SetNullChild.__centauro_ref_markers__["parent"]
        assert marker.on_delete == "set_null"

    def test_invalid_on_delete_value_raises_at_construction(self):
        with pytest.raises(ValueError, match="on_delete"):
            parent_ref(RestrictParent, on_delete="ignore")  # type: ignore[arg-type]

    def test_cascade_class_definition_succeeds(self):
        # If we got here, the class definition above succeeded — the
        # NIE for cascade only fires at delete time, not class-creation.
        assert CascadeChild.__centauro_refs__["parent"] is CascadeParent


class TestRestrictDelete:
    def test_restrict_refuses_with_children(self, conn):
        col = Collection(conn, "items")
        p = RestrictParent(label="parent-a")
        col.write_object(p)
        c = RestrictChild(tag="child-of-a")
        col.write_child(c, parent=p)

        with pytest.raises(ParentHasChildrenError, match="RestrictChild"):
            col.delete_object(p)

        # Parent and child both still present.
        assert col.count_objects(RestrictParent) == 1
        assert col.count_objects(RestrictChild) == 1

    def test_restrict_allows_when_no_children(self, conn):
        col = Collection(conn, "items")
        p = RestrictParent(label="lonely")
        col.write_object(p)

        col.delete_object(p)
        assert col.count_objects(RestrictParent) == 0

    def test_explicit_delete_children_then_parent_succeeds(self, conn):
        col = Collection(conn, "items")
        p = RestrictParent(label="parent-b")
        col.write_object(p)
        c = RestrictChild(tag="child-of-b")
        col.write_child(c, parent=p)

        # The recommended migration path: clear children first.
        col.delete_objects(RestrictChild.fields.parent == p.row.id)
        col.delete_object(p)

        assert col.count_objects(RestrictParent) == 0
        assert col.count_objects(RestrictChild) == 0

    def test_restrict_refuses_in_bulk_delete(self, conn):
        col = Collection(conn, "items")
        p1 = RestrictParent(label="one")
        p2 = RestrictParent(label="two")
        col.write_object(p1)
        col.write_object(p2)
        col.write_child(RestrictChild(tag="c2"), parent=p2)

        with pytest.raises(ParentHasChildrenError):
            col.delete_objects(RestrictParent)

        # Atomic at the batch level: neither parent removed.
        assert col.count_objects(RestrictParent) == 2

    def test_restrict_bulk_succeeds_when_filter_misses_parented(self, conn):
        col = Collection(conn, "items")
        p1 = RestrictParent(label="alone")
        p2 = RestrictParent(label="hasChildren")
        col.write_object(p1)
        col.write_object(p2)
        col.write_child(RestrictChild(tag="c2"), parent=p2)

        # Filter selects only the childless parent.
        count = col.delete_objects(RestrictParent.fields.label == "alone")
        assert count == 1
        assert col.count_objects(RestrictParent) == 1


class TestCascadeReserved:
    def test_cascade_raises_nie_at_delete_time(self, conn):
        col = Collection(conn, "items")
        p = CascadeParent(label="cascade-parent")
        col.write_object(p)
        c = CascadeChild(tag="cascade-child")
        col.write_child(c, parent=p)

        with pytest.raises(NotImplementedError, match="cascade"):
            col.delete_object(p)

        # Parent stays — the NIE fires before the DELETE runs.
        assert col.count_objects(CascadeParent) == 1
        assert col.count_objects(CascadeChild) == 1

    def test_cascade_nie_message_points_at_roadmap(self, conn):
        col = Collection(conn, "items")
        p = CascadeParent(label="x")
        col.write_object(p)
        col.write_child(CascadeChild(tag="y"), parent=p)

        with pytest.raises(NotImplementedError) as exc:
            col.delete_object(p)
        message = str(exc.value)
        assert "cascade" in message
        assert "roadmap" in message.lower() or "CHANGELOG" in message

    def test_cascade_allows_delete_when_no_children(self, conn):
        col = Collection(conn, "items")
        p = CascadeParent(label="childless")
        col.write_object(p)

        # No children → no NIE; the delete proceeds.
        col.delete_object(p)
        assert col.count_objects(CascadeParent) == 0


class TestSetNullReserved:
    def test_set_null_raises_nie_at_delete_time(self, conn):
        col = Collection(conn, "items")
        p = SetNullParent(label="setnull-parent")
        col.write_object(p)
        c = SetNullChild(tag="setnull-child")
        col.write_child(c, parent=p)

        with pytest.raises(NotImplementedError, match="set_null"):
            col.delete_object(p)

        assert col.count_objects(SetNullParent) == 1
        assert col.count_objects(SetNullChild) == 1

    def test_set_null_nie_message_points_at_roadmap(self, conn):
        col = Collection(conn, "items")
        p = SetNullParent(label="x")
        col.write_object(p)
        col.write_child(SetNullChild(tag="y"), parent=p)

        with pytest.raises(NotImplementedError) as exc:
            col.delete_object(p)
        message = str(exc.value)
        assert "set_null" in message
        assert "roadmap" in message.lower() or "CHANGELOG" in message
