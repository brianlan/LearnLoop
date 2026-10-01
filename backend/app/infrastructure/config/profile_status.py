"""Shared "is this VLM profile configured" rule.

The predicate below (``profile_field_unconfigured``) moved verbatim from
``app.infrastructure.vlm.variant_client`` (issue #652) so the settings payload
and the variant clients judge placeholder values with one copy of the rule.
``variant_client`` imports it aliased as ``_profile_unconfigured``, which
keeps the pinned import path (``variant_client._profile_unconfigured``)
working.
"""

from urllib.parse import urlparse

# Placeholder used by the settings defaults and .env.example for unconfigured
# profiles; endpoints on the reserved .invalid TLD are equally non-configured.
_PROFILE_PLACEHOLDER = "replace-me"

PROFILE_STATUS_CONFIGURED = "configured"
PROFILE_STATUS_UNCONFIGURED = "unconfigured"
PROFILE_STATUS_MISCONFIGURED = "misconfigured"

# Every VLM profile prefix, in settings-payload order; single source shared by
# the settings payload and the health probe so a 12th profile cannot drift.
VLM_PROFILE_PREFIXES = (
    "helper_vlm",
    "math_ingestion_vlm",
    "english_ingestion_vlm",
    "grading_vlm",
    "math_solution_vlm",
    "english_solution_vlm",
    "math_coaching_vlm",
    "english_coaching_vlm",
    "variant_generator_vlm",
    "variant_validator_vlm",
    "variant_validator2_vlm",
)


def profile_field_unconfigured(value: str | None) -> bool:
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


def profile_status(
    *, endpoint: str | None, model: str | None, api_key: str | None
) -> str:
    """Three-state configuration health of a VLM profile.

    Delegates every field judgment to ``profile_field_unconfigured`` — no
    duplicated rule. All three fields valid -> configured; all three
    unconfigured -> unconfigured (normal for optional profiles); anything
    partial is an error state -> misconfigured.
    """
    fields = (endpoint, model, api_key)
    unconfigured = [profile_field_unconfigured(value) for value in fields]
    if all(unconfigured):
        return PROFILE_STATUS_UNCONFIGURED
    if any(unconfigured):
        return PROFILE_STATUS_MISCONFIGURED
    return PROFILE_STATUS_CONFIGURED
