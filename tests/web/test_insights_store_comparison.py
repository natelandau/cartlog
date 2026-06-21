"""Tests for the store-comparison insight route and context."""

from __future__ import annotations


def test_store_comparison_auto_picks_two_stores(app_client):
    """Verify the page auto-selects the two seeded stores and renders the comparison toolbar."""
    # When loading the store-comparison insight with no params
    response = app_client.get("/insights/store-comparison", headers={"HX-Request": "true"})

    # Then it renders the fragment with both seeded store names available to select
    assert response.status_code == 200
    assert 'data-insight-view="store-comparison"' in response.text
    assert "Safeway" in response.text
    assert "Costco" in response.text


def test_store_comparison_rejects_bad_scale(app_client):
    """Verify an unknown scale value is a 422, not a silently wrong render."""
    # When requesting an invalid scale
    response = app_client.get(
        "/insights/store-comparison", params={"scale": "bogus"}, headers={"HX-Request": "true"}
    )

    # Then validation fails
    assert response.status_code == 422


def test_store_comparison_round_trips_filters(app_client):
    """Verify selected stores and scale survive into the rendered control state."""
    # Given the two seeded store ids (1 and 2 in insertion order)
    # When requesting an explicit pair on the dollar scale
    response = app_client.get(
        "/insights/store-comparison",
        params={"store_a": 1, "store_b": 2, "scale": "dollar"},
        headers={"HX-Request": "true"},
    )

    # Then the page renders successfully with the dollar scale active
    assert response.status_code == 200
    assert 'value="dollar"' in response.text


def test_store_comparison_empty_date_params_do_not_422(app_client):
    """Verify blank from/to date values (always sent by the toolbar form) are treated as absent."""
    # When the form submits empty date inputs alongside other filters
    response = app_client.get(
        "/insights/store-comparison",
        params={"store_a": 1, "store_b": 2, "from": "", "to": "", "scale": "dollar"},
        headers={"HX-Request": "true"},
    )

    # Then the request succeeds instead of failing date validation with a 422
    assert response.status_code == 200
    assert 'value="dollar"' in response.text


def test_store_comparison_renders_table_and_controls(app_client):
    """Verify the fragment renders the toolbar controls and a comparison region, no Plotly."""
    # When loading the fragment
    response = app_client.get("/insights/store-comparison", headers={"HX-Request": "true"})

    # Then the store selects, the two toggles, the sort control, and the table region are present
    assert response.status_code == 200
    body = response.text
    assert 'name="store_a"' in body
    assert 'name="store_b"' in body
    assert 'name="scale"' in body
    assert 'name="basis"' in body
    assert 'name="sort"' in body
    assert 'id="sc-table"' in body
    # This page is server-rendered: it must not pull in Plotly or register a JS renderer
    assert "plotly" not in body.lower()
    assert "Insights.register" not in body


def test_store_comparison_no_register_script(app_client):
    """Verify the fragment carries no inline chart-registration script (server-rendered)."""
    # When loading the fragment
    response = app_client.get("/insights/store-comparison", headers={"HX-Request": "true"})

    # Then there is no <script> block (other insights self-register; this one does not)
    assert "<script" not in response.text


def test_store_comparison_fragment_renders_comparable_row_without_error():
    """Verify the fragment renders a comparable row (bar + name) without a macro-order error."""
    # Given a comparison with one comparable row (the seed produces none, so build one)
    from decimal import Decimal  # noqa: PLC0415

    from cartlog.analytics.results import (  # noqa: PLC0415
        PriceBasis,
        ScaleMode,
        StorePairComparison,
        StorePairRow,
        StorePairSort,
    )
    from cartlog.web.templating import templates  # noqa: PLC0415

    sc = StorePairComparison(
        store_a="Acut, A",
        store_b="Bmart, B",
        store_a_id=1,
        store_b_id=2,
        scale=ScaleMode.PERCENT,
        basis=PriceBasis.TYPICAL,
        sort=StorePairSort.ALPHABETICAL,
        rows=[
            StorePairRow(
                canonical_name="milk",
                measure_dimension="volume",
                price_a=Decimal("0.003000"),
                price_b=Decimal("0.003500"),
                abs_diff=Decimal("0.000500"),
                pct_diff=16.67,
                pricier="b",
                bar_fraction=1.0,
            )
        ],
        only_a=[],
        only_b=[],
        mismatched=[],
        unmatched_count=0,
        product_options=["milk"],
        category_options=[(1, "dairy")],
        axis_max_pct=16.67,
        dollar_group_max={},
    )

    # When rendering the fragment template directly through the configured environment
    html = templates.env.get_template("insights/_store_comparison.html").render(
        sc=sc,
        store_options=[],
        unit_system="imperial",
        selected_products=[],
        selected_categories=[],
        date_from=None,
        date_to=None,
    )

    # Then the row renders: product name present and a bar width style emitted (no UndefinedError)
    assert "milk" in html
    assert "width:" in html
    # And the bar is the cost (red) color, while the cheaper store's price is highlighted green
    assert "bg-error" in html
    assert "text-success" in html


def test_store_comparison_fragment_renders_dollar_mode_with_dimension_header():
    """Verify the dollar-scale groupby path renders dimension sub-headers without error."""
    # Given a comparison with one comparable row in dollar scale mode
    from decimal import Decimal  # noqa: PLC0415

    from cartlog.analytics.results import (  # noqa: PLC0415
        PriceBasis,
        ScaleMode,
        StorePairComparison,
        StorePairRow,
        StorePairSort,
    )
    from cartlog.web.templating import templates  # noqa: PLC0415

    sc = StorePairComparison(
        store_a="Acut, A",
        store_b="Bmart, B",
        store_a_id=1,
        store_b_id=2,
        scale=ScaleMode.DOLLAR,
        basis=PriceBasis.TYPICAL,
        sort=StorePairSort.ALPHABETICAL,
        rows=[
            StorePairRow(
                canonical_name="milk",
                measure_dimension="volume",
                price_a=Decimal("0.003000"),
                price_b=Decimal("0.003500"),
                abs_diff=Decimal("0.000500"),
                pct_diff=16.67,
                pricier="b",
                bar_fraction=1.0,
            )
        ],
        only_a=[],
        only_b=[],
        mismatched=[],
        unmatched_count=0,
        product_options=["milk"],
        category_options=[(1, "dairy")],
        axis_max_pct=16.67,
        dollar_group_max={},
    )

    # When rendering in dollar scale mode
    html = templates.env.get_template("insights/_store_comparison.html").render(
        sc=sc,
        store_options=[],
        unit_system="imperial",
        selected_products=[],
        selected_categories=[],
        date_from=None,
        date_to=None,
    )

    # Then the dimension sub-header "Volume" appears (from the groupby path) and row renders
    assert "Volume" in html
    assert "milk" in html


def test_store_comparison_fragment_links_unmatched_products_to_search():
    """Verify a non-comparable product links to a prefilled Search and shows the fixable reason."""
    # Given a comparison whose only entry is a both-stores product missing a unit size
    from cartlog.analytics.results import (  # noqa: PLC0415
        PriceBasis,
        ScaleMode,
        StorePairComparison,
        StorePairSort,
        StorePairUnmatched,
    )
    from cartlog.web.templating import templates  # noqa: PLC0415

    sc = StorePairComparison(
        store_a="Acut, A",
        store_b="Bmart, B",
        store_a_id=1,
        store_b_id=2,
        scale=ScaleMode.PERCENT,
        basis=PriceBasis.TYPICAL,
        sort=StorePairSort.ALPHABETICAL,
        rows=[],
        only_a=[],
        only_b=[],
        mismatched=[
            StorePairUnmatched(
                canonical_name="pasta", measure_dimension=None, price=None, reason="needs_unit"
            )
        ],
        unmatched_count=1,
        product_options=[],
        category_options=[],
        axis_max_pct=1.0,
        dollar_group_max={},
    )

    # When rendering the fragment
    html = templates.env.get_template("insights/_store_comparison.html").render(
        sc=sc,
        store_options=[],
        unit_system="imperial",
        selected_products=[],
        selected_categories=[],
        date_from=None,
        date_to=None,
    )

    # Then the fixable group is labeled and the product deep-links to its line items in Search
    assert "Add a unit size to compare" in html
    assert "/search?q=pasta" in html


def test_store_comparison_fragment_has_accessible_table_structure(app_client):
    """Verify the comparison region exposes ARIA table roles for assistive technology."""
    # Given the app is seeded with at least two stores and comparable products
    # When loading the store-comparison fragment
    response = app_client.get("/insights/store-comparison", headers={"HX-Request": "true"})

    # Then the comparison container and its header expose accessible table semantics
    assert response.status_code == 200
    body = response.text
    assert 'role="table"' in body
    assert 'role="row"' in body
    assert 'role="columnheader"' in body


def test_store_comparison_invalid_store_id_falls_back(app_client):
    """Verify a bogus store_a id falls back to a real store rather than rendering an empty selection."""
    # Given a store_a that does not correspond to any seeded store
    # When requesting the fragment with an invalid store id
    response = app_client.get(
        "/insights/store-comparison",
        params={"store_a": 99999},
        headers={"HX-Request": "true"},
    )

    # Then the response is 200 and a real seeded store name appears (fallback was applied)
    assert response.status_code == 200
    body = response.text
    assert "Safeway" in body or "Costco" in body


def test_store_comparison_fragment_row_aria_label():
    """Verify each comparable row carries an aria-label summarizing product, prices, and pricier store."""
    # Given a comparison with one comparable row where store B is pricier
    from decimal import Decimal  # noqa: PLC0415

    from cartlog.analytics.results import (  # noqa: PLC0415
        PriceBasis,
        ScaleMode,
        StorePairComparison,
        StorePairRow,
        StorePairSort,
    )
    from cartlog.web.templating import templates  # noqa: PLC0415

    sc = StorePairComparison(
        store_a="Acut, A",
        store_b="Bmart, B",
        store_a_id=1,
        store_b_id=2,
        scale=ScaleMode.PERCENT,
        basis=PriceBasis.TYPICAL,
        sort=StorePairSort.ALPHABETICAL,
        rows=[
            StorePairRow(
                canonical_name="milk",
                measure_dimension="volume",
                price_a=Decimal("0.003000"),
                price_b=Decimal("0.003500"),
                abs_diff=Decimal("0.000500"),
                pct_diff=16.67,
                pricier="b",
                bar_fraction=1.0,
            )
        ],
        only_a=[],
        only_b=[],
        mismatched=[],
        unmatched_count=0,
        product_options=["milk"],
        category_options=[(1, "dairy")],
        axis_max_pct=16.67,
        dollar_group_max={},
    )

    # When rendering the fragment
    html = templates.env.get_template("insights/_store_comparison.html").render(
        sc=sc,
        store_options=[],
        unit_system="imperial",
        selected_products=[],
        selected_categories=[],
        date_from=None,
        date_to=None,
    )

    # Then each row has an aria-label naming the product, both stores, and which is pricier
    assert 'aria-label="milk:' in html
    assert "Bmart, B is pricier" in html
