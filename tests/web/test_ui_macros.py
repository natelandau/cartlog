"""Tests for shared UI macros rendered through the app Jinja environment."""

from __future__ import annotations

from cartlog.web.templating import templates


def _render(source: str) -> str:
    return templates.env.from_string(source).render()


def test_page_header_renders_title_and_subtitle():
    """Verify page_header emits a .page-title and a .meta subtitle."""
    # When rendering the macro with a subtitle
    html = _render(
        "{% import 'macros/ui.html' as ui %}{{ ui.page_header('Dashboard', 'Spending overview') }}"
    )

    # Then the title and subtitle appear with their role classes
    assert 'class="page-title"' in html
    assert "Dashboard" in html
    assert 'class="meta"' in html
    assert "Spending overview" in html


def test_card_renders_section_title_and_body():
    """Verify card emits a .surface-card wrapper, a .section-title, and the caller body."""
    # When rendering the card macro via a call block
    html = _render(
        "{% import 'macros/ui.html' as ui %}{% call ui.card('Stores') %}<p>body</p>{% endcall %}"
    )

    # Then the surface, title, and body content are present
    assert "surface-card" in html
    assert 'class="section-title"' in html
    assert "Stores" in html
    assert "<p>body</p>" in html
