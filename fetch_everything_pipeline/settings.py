"""Define settings for the fetch everything pipeline."""

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from fer.config import Settings as FERSettings
from pydantic import AliasChoices, Field, HttpUrl, SecretStr
from pydantic_settings import (
    AzureKeyVaultSettingsSource,
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsError,
)


class SettingsLoadError(Exception):
    """Raised when settings cannot be loaded."""


class PipelineSettings(FERSettings):
    """
    Settings for the pipeline, based on FER's `Settings`.

    The robot and storage fields that FER requires are optional here, because
    this tool does not talk to the Destiny repository, and only needs the
    storage account and container when uploading. All other fields, including
    the provider credentials, are inherited from FER unchanged.

    When `key_vault_url` is set, any field can also come from a secret in that
    vault, named after the field with dashes for underscores (for example
    `openalex-key`). Environment variables and `.env` take precedence over the
    vault.

    Attributes:
        destiny_repository_url: Unused by this tool.
        STORAGE_BLOB_ACCOUNT: Storage account name, needed only for upload.
        STORAGE_BLOB_CONTAINER: Container name, needed only for upload.
        STORAGE_BLOB_ACCOUNT_KEY: Unused; upload uses `DefaultAzureCredential`.
        fetch_pipeline_run_id: Optional run ID used to identify manifest uploads.
        key_vault_url: Optional Azure Key Vault to read settings from.

    """

    destiny_repository_url: HttpUrl | None = None
    STORAGE_BLOB_ACCOUNT: str | None = Field(
        default=None, description="The account for the Azure Storage Blob"
    )
    STORAGE_BLOB_CONTAINER: str | None = Field(
        default=None, description="The container for the Azure Storage Blob"
    )
    STORAGE_BLOB_ACCOUNT_KEY: SecretStr | None = Field(
        default=None, description="The account key for the Azure Storage Blob"
    )
    fetch_pipeline_run_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FETCH_PIPELINE_RUN_ID", "AZUREML_RUN_ID"),
        description="Run identifier used to name the uploaded results manifest.",
    )
    key_vault_url: HttpUrl | None = Field(
        default=None,
        description="Azure Key Vault to read settings from, e.g. provider keys.",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Add Azure Key Vault as a settings source when `key_vault_url` is set.

        Called by pydantic-settings each time the settings are loaded,
        not by this code, so isn't actually unused.

        The vault comes after the environment and `.env`, so a variable set
        there overrides the vault's secret. Secrets are fetched with
        `DefaultAzureCredential`: `az login` locally, or managed
        identity in Azure ML.

        Args:
            settings_cls (type[BaseSettings]): The settings class being loaded.
            init_settings (PydanticBaseSettingsSource): Values passed to the
                constructor.
            env_settings (PydanticBaseSettingsSource): Environment variables.
            dotenv_settings (PydanticBaseSettingsSource): The `.env` file.
            file_secret_settings (PydanticBaseSettingsSource): Secret files.

        Returns:
            tuple[PydanticBaseSettingsSource, ...]: The sources, highest
                priority first.

        """
        key_vault_url = (
            init_settings().get("key_vault_url")
            or env_settings().get("key_vault_url")
            or dotenv_settings().get("key_vault_url")
        )
        if not key_vault_url:
            return (init_settings, env_settings, dotenv_settings, file_secret_settings)
        key_vault_settings = AzureKeyVaultSettingsSource(
            settings_cls,
            url=str(key_vault_url),
            credential=DefaultAzureCredential(),
            dash_to_underscore=True,
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            key_vault_settings,
            file_secret_settings,
        )


def load_settings() -> PipelineSettings:
    """
    Load the pipeline settings.

    Returns:
        PipelineSettings: Settings from the environment, `.env` and, when
            `key_vault_url` is set, Azure Key Vault.

    Raises:
        SettingsLoadError: If the settings cannot be read,
            or failures in secret listing and reading.
        ValidationError: If a setting has an invalid value.

    """
    try:
        return PipelineSettings()
    except (AzureError, SettingsError) as settings_error:
        cause = settings_error.__cause__
        detail = cause if isinstance(cause, AzureError) else settings_error
        error_message = f"Cannot load settings: {detail}"
        raise SettingsLoadError(error_message) from settings_error
