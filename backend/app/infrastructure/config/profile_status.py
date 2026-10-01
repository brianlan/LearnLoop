"""Shared "is this VLM profile configured" rule.

The predicate below moved verbatim from
``app.infrastructure.vlm.variant_client`` (issue #652) so the settings payload
and the variant clients judge placeholder values with one copy of the rule.
``variant_client`` re-imports the predicate, which keeps the pinned import
path (``variant_client._profile_unconfigured``) working.
"""

from urllib.parse import urlparse

# Placeholder used by the settings defaults and .env.example for unconfigured
# profiles; endpoints on the reserved .invalid TLD are equally non-configured.
_PROFILE_PLACEHOLDER = "replace-me"

PROFILE_STATUS_CONFIGURED = "configured"
PROFILE_STATUS_UNCONFIGURED = "unconfigured"
PROFILE_STATUS_MISCONFIGURED = "misconfigured"


def _profile_unconfigured(value: str | None) -> bool:
    cleaned = (value or "").strip()
    if not cleaned or cleaned == _PROFILE_PLACEHOLDER:
        return True
    # Endpoint defaults live on the reserved .invalid TLD but carry URL paths
    # (e.g. https://example-…-provider.invalid/api), so judge the hostname
    # label; non-URL values (model/api key) fall back to the raw suffix.
    host = urlparse(cleaned).hostname or ""
    if host:
        return host.lower().endswith(".invalid")
    return cleaned.lower().endswith(".invalid")


def profile_field_unconfigured(value: str | None) -> bool:
    """Public alias of the shared per-field predicate (single copy of the rule)."""
    return _profile_unconfigured(value)


def profile_status(
    *, endpoint: str | None, model: str | None, api_key: str | None
) -> str:
    """Three-state configuration health of a VLM profile.

    Delegates every field judgment to ``_profile_unconfigured`` — no duplicated
    rule. All three fields valid -> configured; all three unconfigured ->
    unconfigured (normal for optional profiles); anything partial is an error
    state -> misconfigured.
    """
    fields = (endpoint, model, api_key)
    unconfigured = [_profile_unconfigured(value) for value in fields]
    if all(unconfigured):
        return PROFILE_STATUS_UNCONFIGURED
    if any(unconfigured):
        return PROFILE_STATUS_MISCONFIGURED
    return PROFILE_STATUS_CONFIGURED
