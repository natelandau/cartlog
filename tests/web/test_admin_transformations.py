"""Tests for the admin transformations list and delete endpoint."""

from __future__ import annotations

from cartlog.db.models import LineItem, Product, ProductMerge
from cartlog.products.service import merge_products


def _seed_rule(app_client, source_name: str = "Coke") -> int:
    """Create a target product + a transformation rule; return the rule id."""
    factory = app_client.app.state.session_factory
    with factory() as session:
        # products.canonical_name is unique, so derive a distinct target per source while
        # keeping "coca cola" as a substring for the listing assertion.
        target = Product(canonical_name=f"coca cola {source_name.strip().lower()}")
        session.add(target)
        session.flush()
        rule = ProductMerge(
            source_name=source_name,
            source_name_normalized=source_name.strip().lower(),
            target_product_id=target.id,
        )
        session.add(rule)
        session.commit()
        return rule.id


def test_admin_transformations_lists_rules(app_client) -> None:
    """Verify saved rules render with their source term and target product."""
    # Given a saved rule
    _seed_rule(app_client)

    # When the transformations page is requested
    response = app_client.get("/admin/transformations")

    # Then the source term and target appear
    assert response.status_code == 200
    assert "Coke" in response.text
    assert "coca cola" in response.text


def test_admin_transformations_filters(app_client) -> None:
    """Verify the filter narrows rules by source term."""
    # Given two rules
    _seed_rule(app_client, source_name="Coke")
    _seed_rule(app_client, source_name="Pepsi")

    # When filtering for "pep" over htmx
    response = app_client.get("/admin/transformations?q=pep", headers={"HX-Request": "true"})

    # Then only the matching rule shows
    assert "Pepsi" in response.text
    assert "Coke" not in response.text


def test_admin_transformation_delete_removes_rule(app_client) -> None:
    """Verify deleting a rule removes it without touching merged line items."""
    # Given a saved rule
    rule_id = _seed_rule(app_client)

    # When posting the delete
    response = app_client.post(
        f"/admin/transformations/{rule_id}/delete", headers={"HX-Request": "true"}
    )

    # Then it succeeds and the rule is gone
    assert response.status_code == 200
    factory = app_client.app.state.session_factory
    with factory() as session:
        assert session.get(ProductMerge, rule_id) is None


def test_admin_transformation_delete_does_not_backdate(app_client) -> None:
    """Verify deleting a rule leaves already-merged line items pointing at the target."""
    factory = app_client.app.state.session_factory
    # Given "eggs" merged into "milk" (eggs' line items now belong to milk) and the saved rule
    with factory() as session:
        eggs = session.query(Product).filter_by(canonical_name="eggs").one()
        milk = session.query(Product).filter_by(canonical_name="milk").one()
        milk_id = milk.id
        merge_products(session, source_id=eggs.id, target_id=milk.id)
        session.commit()
        rule_id = session.query(ProductMerge).filter_by(source_name_normalized="eggs").one().id
        merged_count = session.query(LineItem).filter_by(product_id=milk_id).count()

    # When the transformation rule is deleted
    response = app_client.post(
        f"/admin/transformations/{rule_id}/delete", headers={"HX-Request": "true"}
    )

    # Then the rule is gone but the already-merged line items still belong to the target
    assert response.status_code == 200
    with factory() as session:
        assert session.get(ProductMerge, rule_id) is None
        assert session.query(LineItem).filter_by(product_id=milk_id).count() == merged_count
