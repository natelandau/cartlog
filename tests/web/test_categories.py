"""Tests for the category management UI."""

from __future__ import annotations

from cartlog.categories.service import CategoryService
from cartlog.db.models import Category, Product


def _seed_taxonomy(app_client) -> None:
    # Use names distinct from the analytics seed data ("dairy", "produce") to avoid conflicts.
    factory = app_client.app.state.session_factory
    with factory() as session:
        svc = CategoryService(session)
        svc.create_category(name="beverages")
        svc.create_category(name="juice")
        svc.ensure_uncategorized()
        session.commit()


def test_create_category_via_post(app_client) -> None:
    """Verify posting a new category creates it and returns the list."""
    response = app_client.post("/categories", data={"name": "frozen"})
    assert response.status_code == 200
    assert "frozen" in response.text
    factory = app_client.app.state.session_factory
    with factory() as session:
        assert session.query(Category).filter_by(name="frozen").count() == 1


def test_create_duplicate_returns_error(app_client) -> None:
    """Verify creating a duplicate category returns a 422 with an error message."""
    app_client.post("/categories", data={"name": "frozen"})
    response = app_client.post("/categories", data={"name": "frozen"})
    assert response.status_code == 422
    assert "already exists" in response.text


def test_rename_via_post(app_client) -> None:
    """Verify posting a rename updates the category name."""
    factory = app_client.app.state.session_factory
    with factory() as session:
        svc = CategoryService(session)
        cat = svc.create_category(name="frozenz")
        session.commit()
        cat_id = cat.id
    response = app_client.post(f"/categories/{cat_id}/rename", data={"new_name": "frozen foods"})
    assert response.status_code == 200
    assert "frozen foods" in response.text


def test_merge_via_post(app_client) -> None:
    """Verify posting a merge repoints and removes the source category."""
    factory = app_client.app.state.session_factory
    with factory() as session:
        svc = CategoryService(session)
        a = svc.create_category(name="snacksz")
        b = svc.create_category(name="treatsz")
        session.commit()
        a_id, b_id = a.id, b.id
    response = app_client.post(f"/categories/{b_id}/merge", data={"target_id": a_id})
    assert response.status_code == 200
    with factory() as session:
        assert session.get(Category, b_id) is None


def test_delete_with_target_via_post(app_client) -> None:
    """Verify deleting a category with products reassigns them to the chosen target."""
    factory = app_client.app.state.session_factory
    with factory() as session:
        svc = CategoryService(session)
        a = svc.create_category(name="snacksy")
        b = svc.create_category(name="pantryy")
        session.add(Product(canonical_name="chipsy", category_id=a.id))
        session.commit()
        a_id, b_id = a.id, b.id
    response = app_client.post(f"/categories/{a_id}/delete", data={"reassign_to_id": b_id})
    assert response.status_code == 200
    with factory() as session:
        assert session.get(Category, a_id) is None
        assert session.query(Product).filter_by(canonical_name="chipsy").one().category_id == b_id


def test_delete_with_dependents_no_target_returns_error(app_client) -> None:
    """Verify deleting a category with products but no target returns a 422 error."""
    factory = app_client.app.state.session_factory
    with factory() as session:
        svc = CategoryService(session)
        a = svc.create_category(name="snacksw")
        session.add(Product(canonical_name="chipsw", category_id=a.id))
        session.commit()
        a_id = a.id
    response = app_client.post(f"/categories/{a_id}/delete", data={"reassign_to_id": ""})
    assert response.status_code == 422
    assert "reassign" in response.text


def test_categories_page_lists_categories(app_client) -> None:
    """Verify the categories page renders the flat list with the system bucket."""
    # Given a seeded taxonomy
    _seed_taxonomy(app_client)
    # When loading the categories page
    response = app_client.get("/categories")
    # Then it renders every category including the system Uncategorized bucket
    assert response.status_code == 200
    assert "beverages" in response.text
    assert "juice" in response.text
    assert "Uncategorized" in response.text


def test_merge_into_system_category_rejected(app_client) -> None:
    """Verify merging a category into the system Uncategorized bucket is rejected."""
    factory = app_client.app.state.session_factory
    with factory() as session:
        svc = CategoryService(session)
        uncat = svc.ensure_uncategorized()
        src = svc.create_category(name="mergesrc")
        session.commit()
        uncat_id, src_id = uncat.id, src.id
    # When merging a real category into the system bucket
    response = app_client.post(f"/categories/{src_id}/merge", data={"target_id": uncat_id})
    # Then it is rejected and the source survives
    assert response.status_code == 422
    with factory() as session:
        assert session.get(Category, src_id) is not None


def test_inline_create_returns_picker_with_new_selected(app_client) -> None:
    """Verify inline category creation returns a picker with the new category selected."""
    # Given a seeded taxonomy
    _seed_taxonomy(app_client)
    # When creating a category inline
    response = app_client.post("/categories/inline", data={"name": "berries"})
    # Then the response is a picker select containing the new category, selected
    assert response.status_code == 200
    assert "berries" in response.text
    assert "selected" in response.text
    assert 'name="category_id"' in response.text
