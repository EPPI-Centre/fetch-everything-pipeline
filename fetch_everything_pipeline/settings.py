"""Define settings and provider configuration for the fetch everything pipeline."""

from fer.config import ExternalAPI, external_api_priority
from fer.config import Settings as FERSettings
from fer.data_models.crossref import get_crossref_api_config
from fer.data_models.generic import APIConfig, prepare_api_config
from fer.data_models.openalex import get_openalex_api_config
from fer.data_models.scopus import get_scopus_batch_api_config
from fer.data_models.unpaywall import get_unpaywall_api_config
from pydantic import Field, HttpUrl, SecretStr


class PipelineSettings(FERSettings):
    """
    Settings for the pipeline, based on FER's `Settings`.

    The robot and storage fields that FER requires are optional here, because
    this tool does not talk to the Destiny repository, and only needs the
    storage account and container when uploading. All other fields, including
    the provider credentials, are inherited from FER unchanged.

    Attributes:
        destiny_repository_url: Unused by this tool.
        STORAGE_BLOB_ACCOUNT: Storage account name, needed only for upload.
        STORAGE_BLOB_CONTAINER: Container name, needed only for upload.
        STORAGE_BLOB_ACCOUNT_KEY: Unused; upload uses `DefaultAzureCredential`.

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


def prepare_provider_configs(settings: FERSettings) -> dict[str, dict[str, APIConfig]]:
    """
    Build a provider configuration for every provider with credentials.

    Uses the same provider getters as `fer.local.run.prepare_processor`, then
    fer's `prepare_api_config`, which drops any provider whose required API
    key is missing.

    Args:
        settings (FERSettings): Settings holding the provider credentials.

    Returns:
        dict[str, dict[str, APIConfig]]: A configuration mapping, keyed by
            priority name and then provider name, in priority order.

    """
    available_api_configs: list[APIConfig] = [
        get_openalex_api_config(settings),
        get_crossref_api_config(),
        get_unpaywall_api_config(),
        get_scopus_batch_api_config(),
    ]
    return prepare_api_config(api_configs=available_api_configs, settings=settings)


def enabled_providers(settings: FERSettings) -> list[ExternalAPI]:
    """
    List the providers that will be used for fetching, in priority order.

    Args:
        settings (FERSettings): Settings holding the provider credentials.

    Returns:
        list[ExternalAPI]: Providers with the credentials they need.

    """
    provider_configs = prepare_provider_configs(settings)
    return [
        config.name for config in provider_configs[external_api_priority.name].values()
    ]
