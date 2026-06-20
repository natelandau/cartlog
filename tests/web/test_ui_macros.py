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


def test_card_renders_without_call_body():
    """Verify card renders the surface without error when called without a body."""
    # Given a card called without a {% call %} body
    # When rendering the card macro without a call block
    html = _render("{% import 'macros/ui.html' as ui %}{{ ui.card('Empty') }}")

    # Then the surface and title are present without errors
    assert "surface-card" in html
    assert 'class="section-title"' in html
    assert "Empty" in html


def test_icon_renders_known_lucide_svg():
    """Verify icon() emits an aria-hidden svg for a known name and nothing for an unknown one."""
    # When rendering a known and an unknown icon
    known = _render("{% import 'macros/ui.html' as ui %}{{ ui.icon('search') }}")
    unknown = _render("{% import 'macros/ui.html' as ui %}{{ ui.icon('definitely-not-an-icon') }}")

    # Then the known icon is an accessible inline svg and the unknown one is empty
    assert "<svg" in known
    assert 'aria-hidden="true"' in known
    assert "<svg" not in unknown
