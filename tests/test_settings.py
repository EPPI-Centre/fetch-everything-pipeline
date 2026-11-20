"""Tests for the pipeline settings."""

from pathlib import Path

import pytest
from azure.core.exceptions import ClientAuthenticationError
from fer.config import Settings as FERSettings
from pydantic import ValidationError
from pytest_mock import MockerFixture, MockType

from fetch_everything_pipeline.settings import (
    PipelineSettings,
    SettingsLoadError,
    load_settings,
)
from tests.fakes import fill_vault

TEST_KEY_VAULT_URL = "https://test-vault.vault.azure.net/"


@pytest.fixture
def mock_secret_client(mocker: MockerFixture) -> MockType:
    """
    Patch Key Vault's `SecretClient` and the credential, so no Azure calls are made.

    The vault starts empty; use `fill_vault` to add secrets.

    Returns:
        MockType: The patched `SecretClient` class.

    """
    mocker.patch("fetch_everything_pipeline.settings.DefaultAzureCredential")
    secret_client = mocker.patch("azure.keyvault.secrets.SecretClient")
    fill_vault(secret_client, {})
    return secret_client


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


@pytest.mark.usefixtures("test_settings")
def test_does_not_use_key_vault_without_url(mock_secret_client: MockType):
    settings = PipelineSettings()

    assert settings.key_vault_url is None
    mock_secret_client.assert_not_called()


@pytest.mark.usefixtures("test_settings")
def test_reads_provider_keys_from_key_vault(
    mocker: MockerFixture,
    mock_secret_client: MockType,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("OPENALEX_KEY")
    monkeypatch.setenv("KEY_VAULT_URL", TEST_KEY_VAULT_URL)
    fill_vault(
        mock_secret_client,
        {
            "openalex-key": "vault-openalex-key",
            "elsevier-scopus-key": "vault-scopus-key",
            "elsevier-scopus-inst-token": "vault-inst-token",
        },
    )

    settings = PipelineSettings()

    mock_secret_client.assert_called_once_with(
        vault_url=TEST_KEY_VAULT_URL, credential=mocker.ANY
    )
    assert settings.openalex_key is not None
    assert settings.openalex_key.get_secret_value() == "vault-openalex-key"
    assert settings.elsevier_scopus_key is not None
    assert settings.elsevier_scopus_key.get_secret_value() == "vault-scopus-key"
    assert settings.elsevier_scopus_inst_token is not None
    assert settings.elsevier_scopus_inst_token.get_secret_value() == "vault-inst-token"


@pytest.mark.usefixtures("test_settings")
def test_environment_overrides_key_vault(
    mock_secret_client: MockType, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KEY_VAULT_URL", TEST_KEY_VAULT_URL)
    fill_vault(mock_secret_client, {"openalex-key": "vault-openalex-key"})

    settings = PipelineSettings()

    assert settings.openalex_key is not None
    assert (
        settings.openalex_key.get_secret_value() == "openalex-test-key"
    ), "OPENALEX_KEY from the environment should win over the vault"


def test_reads_key_vault_url_from_dotenv(
    test_settings: PipelineSettings,
    mock_secret_client: MockType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.delenv("OPENALEX_KEY")
    (tmp_path / ".env").write_text(
        f"KEY_VAULT_URL={TEST_KEY_VAULT_URL}\n", encoding="utf-8"
    )
    fill_vault(mock_secret_client, {"openalex-key": "vault-openalex-key"})

    monkeypatch.chdir(tmp_path)
    settings = PipelineSettings()

    assert str(settings.key_vault_url) == TEST_KEY_VAULT_URL
    assert settings.openalex_key is not None
    assert settings.openalex_key.get_secret_value() == "vault-openalex-key"


@pytest.mark.usefixtures("test_settings")
def test_load_settings_reports_key_vault_errors(
    mock_secret_client: MockType, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KEY_VAULT_URL", TEST_KEY_VAULT_URL)
    mock_secret_client.return_value.list_properties_of_secrets.side_effect = (
        ClientAuthenticationError("access denied")
    )

    with pytest.raises(SettingsLoadError, match="access denied"):
        load_settings()


@pytest.mark.usefixtures("test_settings")
def test_load_settings_passes_through_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MAILTO", "not an email")

    with pytest.raises(ValidationError):
        load_settings()
