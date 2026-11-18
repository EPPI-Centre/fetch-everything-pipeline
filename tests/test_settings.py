"""Tests for the pipeline settings."""

import pytest
from fer.config import Settings as FERSettings
from pydantic import ValidationError

from fetch_everything_pipeline.settings import PipelineSettings


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
    ("environment_variable", "expected_run_id"),
    [
        ("FETCH_PIPELINE_RUN_ID", "pipeline-run-123"),
        ("AZUREML_RUN_ID", "azureml-run-456"),
    ],
)
def test_loads_run_id_from_environment(
    test_settings: PipelineSettings,
    monkeypatch: pytest.MonkeyPatch,
    environment_variable: str,
    expected_run_id: str,
):
    monkeypatch.setenv(environment_variable, expected_run_id)

    assert PipelineSettings().fetch_pipeline_run_id == expected_run_id
