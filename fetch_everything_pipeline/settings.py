"""Define settings for the fetch everything pipeline."""

from fer.config import Settings as FERSettings
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
