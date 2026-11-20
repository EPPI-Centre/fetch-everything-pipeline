"""Tests for the pipeline settings."""

from pathlib import Path

import pytest
from azure.core.exceptions import ClientAuthenticationError
from fer.config import Settings as FERSettings
from pydantic import ValidationError
from pydantic_settings import SettingsError
from pytest_mock import MockerFixture, MockType

from fetch_everything_pipeline.settings import (
    PipelineSettings,
    SettingsLoadError,
    load_settings,
)
from tests.fakes import fill_vault

TEST_KEY_VAULT_URL = "https://test-vault.vault.azure.net/"


def raise_settings_error_from(cause: Exception) -> None:
    """
    Raise a `SettingsError` chained from `cause`, as pydantic-settings does.

    pydantic-settings wraps an error from a field's lookup in `SettingsError`
    Failures listing secrets arrive unwrapped.
    Failures reading a secret arrive as `SettingsError`.

    This function simulates that behavior for testing purposes.

    Args:
        cause (Exception): The error to chain from.

    Raises:
        SettingsError: Always raised with `cause` as its `__cause__`.

    """
    error_message = "error getting value for field from Key Vault"
    raise SettingsError(error_message) from cause


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


def test_scopus_off_by_default(test_settings: PipelineSettings):
    assert test_settings.use_scopus is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("false", False),
        ("False", False),
        ("0", False),
    ],
)
@pytest.mark.usefixtures("test_settings")
def test_loads_use_scopus_from_environment(
    monkeypatch: pytest.MonkeyPatch, value: str, *, expected: bool
):
    """Azure ML renders boolean inputs as `True`/`False` in the command."""
    monkeypatch.setenv("USE_SCOPUS", value)

    assert PipelineSettings().use_scopus is expected


@pytest.mark.usefixtures("test_settings")
def test_rejects_invalid_use_scopus(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("USE_SCOPUS", "not_a_bool")

    with pytest.raises(ValidationError):
        PipelineSettings()


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
def test_load_settings_reports_key_vault_errors_reading_secrets(
    mock_secret_client: MockType, monkeypatch: pytest.MonkeyPatch
):
    """
    Test that `load_settings` reports key vault errors when reading secrets.

    Listing succeeds but `get_secret` fails, pydantic-settings
    wraps that in a `SettingsError` unlike listing failures.

    """
    monkeypatch.delenv("OPENALEX_KEY")
    monkeypatch.setenv("KEY_VAULT_URL", TEST_KEY_VAULT_URL)
    fill_vault(mock_secret_client, {"openalex-key": "dummy-vault-openalex-key"})
    mock_secret_client.return_value.get_secret.side_effect = ClientAuthenticationError(
        "read permission denied"
    )

    with pytest.raises(SettingsLoadError, match="read permission denied"):
        load_settings()


def test_load_settings_translates_azure_caused_settings_error(mocker: MockerFixture):
    """
    Test that `load_settings` translates an Azure-caused settings error.

    Checks that this is translated into a `SettingsLoadError`.

    """
    azure_error = ClientAuthenticationError("get permission denied")
    mocker.patch(
        "fetch_everything_pipeline.settings.PipelineSettings",
        side_effect=lambda: raise_settings_error_from(azure_error),
    )
    with pytest.raises(SettingsLoadError, match="get permission denied") as excinfo:
        load_settings()

    assert isinstance(excinfo.value.__cause__, SettingsError)
    assert excinfo.value.__cause__.__cause__ is azure_error


def test_load_settings_reports_unrelated_settings_errors(mocker: MockerFixture):
    """Test that `load_settings` reports non-Azure settings errors with their own text."""
    mocker.patch(
        "fetch_everything_pipeline.settings.PipelineSettings",
        side_effect=lambda: raise_settings_error_from(ValueError("unrelated error")),
    )
    with pytest.raises(SettingsLoadError, match="error getting value") as excinfo:
        load_settings()

    assert isinstance(excinfo.value.__cause__, SettingsError)
    assert str(excinfo.value.__cause__.__cause__) == "unrelated error"


@pytest.mark.usefixtures("test_settings")
def test_load_settings_passes_through_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MAILTO", "not an email")

    with pytest.raises(ValidationError):
        load_settings()
