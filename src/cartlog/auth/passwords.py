"""Password policy: minimum length plus a small common-password blocklist."""

from __future__ import annotations

MIN_PASSWORD_LENGTH = 12

# A short blocklist of obvious choices. Kept intentionally small; length is the main defense.
_COMMON = frozenset(
    {
        "password1234",
        "123456789012",
        "qwertyuiop12",
        "letmeinplease",
        "iloveyou1234",
        "adminadmin12",
        "cartlogadmin",
    }
)


def validate_password(plain: str) -> str | None:
    """Return an error message if the password violates policy, else None."""
    if len(plain) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if plain.lower() in _COMMON:
        return "That password is too common. Try a different one."
    return None
