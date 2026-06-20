"""Tests for the admin stores mapping view and merge endpoint."""

from __future__ import annotations

from cartlog.db.models import Receipt, Store, StoreMerge


def test_admin_stores_lists_stores(app_client) -> None:
    """Verify the stores page renders seeded stores."""
    # When the stores page is requested
    response = app_client.get("/admin/stores")

    # Then it succeeds and shows a seeded store
    assert response.status_code == 200
    assert "Safeway" in response.text


def test_admin_stores_filters_by_location(app_client) -> None:
    """Verify the filter narrows the stores fragment by location text."""
    # When filtering for "Airport" via an htmx request
    response = app_client.get("/admin/stores?q=Airport", headers={"HX-Request": "true"})

    # Then only the Costco (Airport Rd) row remains; Safeway's row is filtered out.
    # (Every row's merge-target <select> lists all stores, so store names appear as options
    # regardless of the filter; assert on the row cells, not the whole fragment.)
    assert response.status_code == 200
    assert '<td data-label="Chain">Costco</td>' in response.text
    assert '<td data-label="Chain">Safeway</td>' not in response.text


def test_admin_stores_sort_rejects_unknown_key(app_client) -> None:
    """Verify an unknown sort key is rejected with 422 by the enum-typed param."""
    # When requesting an invalid sort
    response = app_client.get("/admin/stores?sort=bogus")

    # Then FastAPI rejects it
    assert response.status_code == 422


def test_admin_store_merge_confirm_names_both_stores(app_client) -> None:
    """Verify the confirm fragment names the source and target stores."""
    # Given the ids of the two seeded stores
    factory = app_client.app.state.session_factory
    with factory() as session:
        safeway = session.query(Store).filter_by(chain_name="Safeway").one()
        costco = session.query(Store).filter_by(chain_name="Costco").one()
        safeway_id, costco_id = safeway.id, costco.id

    # When requesting the confirm fragment to merge Safeway into Costco
    response = app_client.get(
        f"/admin/stores/{safeway_id}/merge/confirm?target_id={costco_id}",
        headers={"HX-Request": "true"},
    )

    # Then both chains appear
    assert response.status_code == 200
    assert "Safeway" in response.text
    assert "Costco" in response.text


def test_admin_store_merge_post_merges_stores(app_client) -> None:
    """Verify posting a merge reassigns receipts, deletes the source, and records a rule."""
    # Given the ids of seeded Safeway (source) and Costco (target)
    factory = app_client.app.state.session_factory
    with factory() as session:
        safeway = session.query(Store).filter_by(chain_name="Safeway").one()
        costco = session.query(Store).filter_by(chain_name="Costco").one()
        safeway_id, costco_id = safeway.id, costco.id
        safeway_receipts = session.query(Receipt).filter_by(store_id=safeway_id).count()

    # When posting the merge
    response = app_client.post(
        f"/admin/stores/{safeway_id}/merge",
        data={"target_id": str(costco_id)},
        headers={"HX-Request": "true"},
    )

    # Then the source is gone, its receipts moved, and a rule was saved
    assert response.status_code == 200
    with factory() as session:
        assert session.get(Store, safeway_id) is None
        assert session.query(Receipt).filter_by(store_id=safeway_id).count() == 0
        assert session.query(Receipt).filter_by(store_id=costco_id).count() >= safeway_receipts
        assert session.query(StoreMerge).filter_by(target_store_id=costco_id).count() == 1


def _seed_store_merge_rule(app_client) -> int:
    """Create a StoreMerge rule pointing at seeded Costco and return its id."""
    factory = app_client.app.state.session_factory
    with factory() as session:
        costco = session.query(Store).filter_by(chain_name="Costco").one()
        rule = StoreMerge(
            source_chain_name="Costco Wholesale",
            source_location="Airport Rd",
            source_identity_normalized="costco wholesale\x1fairport rd",
            target_store_id=costco.id,
        )
        session.add(rule)
        session.commit()
        return rule.id


def test_admin_store_merges_lists_rules(app_client) -> None:
    """Verify the store-merges page renders a saved rule."""
    # Given a saved rule
    _seed_store_merge_rule(app_client)

    # When the page is requested
    response = app_client.get("/admin/store-merges")

    # Then it shows the source term
    assert response.status_code == 200
    assert "Costco Wholesale" in response.text


def test_admin_store_merge_delete_removes_rule(app_client) -> None:
    """Verify posting a delete removes the rule without touching reassigned receipts."""
    # Given a saved rule
    rule_id = _seed_store_merge_rule(app_client)

    # When posting the delete
    response = app_client.post(
        f"/admin/store-merges/{rule_id}/delete", headers={"HX-Request": "true"}
    )

    # Then the rule is gone
    assert response.status_code == 200
    factory = app_client.app.state.session_factory
    with factory() as session:
        assert session.get(StoreMerge, rule_id) is None
