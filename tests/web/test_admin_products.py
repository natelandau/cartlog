"""Tests for the admin products mapping view and merge endpoint."""

from __future__ import annotations

from cartlog.db.models import LineItem, Product, ProductMerge


def test_admin_products_lists_products(app_client) -> None:
    """Verify the products page renders seeded products with occurrence counts."""
    # When the products page is requested
    response = app_client.get("/admin/products")

    # Then it succeeds and shows a seeded product
    assert response.status_code == 200
    assert "eggs" in response.text


def test_admin_products_filters_by_name(app_client) -> None:
    """Verify the name filter narrows the products fragment."""
    # When filtering for "egg" via an htmx request
    response = app_client.get("/admin/products?q=egg", headers={"HX-Request": "true"})

    # Then eggs is present and an unrelated product is not
    assert response.status_code == 200
    assert "eggs" in response.text
    assert "bananas" not in response.text


def test_admin_products_sort_rejects_unknown_key(app_client) -> None:
    """Verify an unknown sort key is rejected with 422 by the enum-typed param."""
    # When requesting an invalid sort
    response = app_client.get("/admin/products?sort=bogus")

    # Then FastAPI rejects it
    assert response.status_code == 422


def test_admin_merge_confirm_names_both_products(app_client) -> None:
    """Verify the confirm fragment names the source and target products."""
    # Given the id of seeded "eggs"
    factory = app_client.app.state.session_factory
    with factory() as session:
        eggs = session.query(Product).filter_by(canonical_name="eggs").one()
        eggs_id = eggs.id

    # When requesting the confirm fragment to merge eggs into milk
    response = app_client.get(
        f"/admin/products/{eggs_id}/merge/confirm?target=milk",
        headers={"HX-Request": "true"},
    )

    # Then both names appear
    assert response.status_code == 200
    assert "eggs" in response.text
    assert "milk" in response.text


def test_admin_merge_post_merges_products(app_client) -> None:
    """Verify posting a merge reassigns items, deletes the source, and records a rule."""
    # Given the ids of seeded "eggs" (source) and "milk" (target)
    factory = app_client.app.state.session_factory
    with factory() as session:
        eggs = session.query(Product).filter_by(canonical_name="eggs").one()
        milk = session.query(Product).filter_by(canonical_name="milk").one()
        eggs_id, milk_id = eggs.id, milk.id
        eggs_lines = session.query(LineItem).filter_by(product_id=eggs_id).count()

    # When posting the merge
    response = app_client.post(
        f"/admin/products/{eggs_id}/merge",
        data={"target_id": str(milk_id)},
        headers={"HX-Request": "true"},
    )

    # Then the source is gone, its items moved, and a rule was saved
    assert response.status_code == 200
    with factory() as session:
        assert session.get(Product, eggs_id) is None
        assert session.query(LineItem).filter_by(product_id=eggs_id).count() == 0
        assert session.query(LineItem).filter_by(product_id=milk_id).count() >= eggs_lines
        assert session.query(ProductMerge).filter_by(source_name_normalized="eggs").count() == 1
