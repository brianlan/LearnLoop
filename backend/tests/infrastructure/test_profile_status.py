"""Unit tests for the shared VLM profile configured/unconfigured rule (#652).

The predicate itself moved verbatim from ``variant_client`` (the import path
``variant_client._profile_unconfigured`` stays pinned by
``test_variant_vlm.py``); ``profile_status`` must delegate to it rather than
duplicate the rule.
"""

from __future__ import annotations

import pytest

from app.infrastructure.config import profile_status as profile_status_module
from app.infrastructure.config.profile_status import (
    PROFILE_STATUS_CONFIGURED,
    PROFILE_STATUS_MISCONFIGURED,
    PROFILE_STATUS_UNCONFIGURED,
    profile_field_unconfigured,
    profile_status,
)


class TestProfileFieldUnconfigured:
    def test_placeholder_is_unconfigured(self) -> None:
        assert profile_field_unconfigured("replace-me")

    def test_empty_and_whitespace_only_are_unconfigured(self) -> None:
        assert profile_field_unconfigured("")
        assert profile_field_unconfigured(None)
        assert profile_field_unconfigured("   ")

    def test_url_hostname_invalid_tld_is_unconfigured(self) -> None:
        assert profile_field_unconfigured(
            "https://example-variant-generator-vlm-provider.invalid/api"
        )
        assert profile_field_unconfigured("https://placeholder.invalid")

    def test_url_hostname_real_is_configured(self) -> None:
        assert not profile_field_unconfigured("https://api.real-provider.example/v1")

    def test_non_url_values_fall_back_to_raw_suffix(self) -> None:
        assert not profile_field_unconfigured("gpt-real-model")
        assert profile_field_unconfigured("placeholder.invalid")

    def test_placeholder_match_is_exact_case(self) -> None:
        # "Replace-Me" is not the documented placeholder, so it counts as a
        # (probably mistaken) real value: configured, not silently dropped.
        assert not profile_field_unconfigured("Replace-Me")


class TestProfileStatus:
    def test_all_fields_valid_is_configured(self) -> None:
        assert (
            profile_status(
                endpoint="https://api.real-provider.example/v1",
                model="gpt-real-model",
                api_key="sk-real",
            )
            == PROFILE_STATUS_CONFIGURED
        )

    def test_all_fields_unconfigured_is_unconfigured(self) -> None:
        assert (
            profile_status(
                endpoint="https://example-variant-validator2-vlm-provider.invalid/api",
                model="replace-me",
                api_key="replace-me",
            )
            == PROFILE_STATUS_UNCONFIGURED
        )

    def test_whitespace_only_values_count_as_unconfigured(self) -> None:
        assert (
            profile_status(endpoint="   ", model="  ", api_key=None)
            == PROFILE_STATUS_UNCONFIGURED
        )

    def test_partial_configuration_is_misconfigured(self) -> None:
        assert (
            profile_status(
                endpoint="https://api.real-provider.example/v1",
                model="replace-me",
                api_key="sk-real",
            )
            == PROFILE_STATUS_MISCONFIGURED
        )
        assert (
            profile_status(
                endpoint="https://api.real-provider.example/v1",
                model="gpt-real-model",
                api_key="replace-me",
            )
            == PROFILE_STATUS_MISCONFIGURED
        )
        assert (
            profile_status(
                endpoint="https://example-provider.invalid/api",
                model="gpt-real-model",
                api_key="sk-real",
            )
            == PROFILE_STATUS_MISCONFIGURED
        )

    def test_status_delegates_to_field_predicate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No duplicated rule: flipping the shared predicate must flip the
        # three-state computation.
        monkeypatch.setattr(
            profile_status_module,
            "_profile_unconfigured",
            lambda value: True,
        )
        assert (
            profile_status(
                endpoint="https://api.real-provider.example/v1",
                model="gpt-real-model",
                api_key="sk-real",
            )
            == PROFILE_STATUS_UNCONFIGURED
        )
