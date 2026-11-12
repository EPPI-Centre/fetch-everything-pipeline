"""Tests for the pipeline settings and provider configuration."""

from typing import Any

import pytest
from fer.config import ExternalAPI
from fer.config import Settings as FERSettings
from pydantic import SecretStr, ValidationError

from fetch_everything_pipeline.settings import (
    PipelineSettings,
    enabled_providers,
    prepare_provider_configs,
)


def test_loads_with_only_mailto_and_openalex_key(test_settings: PipelineSettings):
    assert test_settings.mailto == "pipeline@example.org"
    assert test_settings.openalex_key is not None
    assert test_settings.openalex_key.get_secret_value() == "openalex-test-key"
    assert test_settings.destiny_repository_url is None
    assert test_settings.STORAGE_BLOB_ACCOUNT is None
    assert test_settings.STORAGE_BLOB_CONTAINER is None
    assert test_settings.STORAGE_BLOB_ACCOUNT_KEY is None


def test_fer_settings_still_require_robot_fields(test_settings: PipelineSettings):
    """Guards the reason for the subclass: fer won't load from the same environment."""
    with pytest.raises(ValidationError):
        FERSettings()


def test_is_accepted_where_fer_settings_are_expected(test_settings: PipelineSettings):
    assert isinstance(test_settings, FERSettings)


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        pytest.param(
            {},
            [ExternalAPI.OPENALEX, ExternalAPI.CROSSREF, ExternalAPI.UNPAYWALL],
            id="scopus-dropped-without-key",
        ),
        pytest.param(
            {"openalex_key": None},
            [ExternalAPI.CROSSREF, ExternalAPI.UNPAYWALL],
            id="openalex-and-scopus-dropped-without-keys",
        ),
        pytest.param(
            {"openalex_key": SecretStr("")},
            [ExternalAPI.CROSSREF, ExternalAPI.UNPAYWALL],
            id="empty-openalex-key-counts-as-missing",
        ),
        pytest.param(
            {"elsevier_scopus_key": SecretStr("scopus-test-key")},
            [
                ExternalAPI.OPENALEX,
                ExternalAPI.CROSSREF,
                ExternalAPI.UNPAYWALL,
                ExternalAPI.SCOPUS,
            ],
            id="all-providers-in-priority-order",
        ),
    ],
)
def test_enabled_providers(
    test_settings: PipelineSettings,
    update: dict[str, Any],
    expected: list[ExternalAPI],
):
    settings = test_settings.model_copy(update=update)
    assert enabled_providers(settings) == expected


def test_provider_configs_carry_credentials(test_settings: PipelineSettings):
    configs = prepare_provider_configs(test_settings)
    openalex = next(
        config
        for providers in configs.values()
        for config in providers.values()
        if config.name is ExternalAPI.OPENALEX
    )

    assert (
        openalex.headers["api_key"] == "openalex-test-key"  # pragma: allowlist secret
    )
    assert openalex.query_params is not None
    assert openalex.query_params["mailto"] == "pipeline@example.org"
