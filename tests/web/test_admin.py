"""Tests for the admin landing page."""

from __future__ import annotations

from decimal import Decimal

from cartlog.clock import naive_utcnow
from cartlog.db.models import ParseCostEvent


def test_admin_index_shows_parsing_cost(app_client) -> None:
    """Verify the admin landing page shows the three LLM-cost figures."""
    # Given a recent parse cost event (within the rolling 30-day window)
    factory = app_client.app.state.session_factory
    with factory() as session:
        session.add(
            ParseCostEvent(
                job_id=None, estimated_cost_usd=Decimal("1.23"), created_at=naive_utcnow()
            )
        )
        session.commit()

    # When loading the admin landing page
    response = app_client.get("/admin")

    # Then the page renders the cost figures
    assert response.status_code == 200
    assert "Total LLM costs" in response.text
    assert "Past 30 days" in response.text
    assert "Avg cost / receipt" in response.text
    assert "$1.23" in response.text
