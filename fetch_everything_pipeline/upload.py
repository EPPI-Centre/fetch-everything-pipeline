"""Upload fetched full texts to Azure Blob Storage."""

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fer.blob_storage import BlobUploadError, FetchEverythingBlobStorageClient
from loguru import logger

from fetch_everything_pipeline.models import PipelineResult, Status
from fetch_everything_pipeline.settings import PipelineSettings


class UploadConfigurationError(Exception):
    """Raised when required blob storage settings are missing."""


class ResultsManifestUploadError(Exception):
    """Raised when the results manifest cannot be uploaded."""


def validate_upload_settings(settings: PipelineSettings) -> None:
    """
    Check that blob storage account and container settings are present.

    Args:
        settings (PipelineSettings): Pipeline settings to validate.

    Raises:
        UploadConfigurationError: If the account or container is missing.

    """
    missing_settings = [
        name
        for name, value in (
            ("STORAGE_BLOB_ACCOUNT", settings.STORAGE_BLOB_ACCOUNT),
            ("STORAGE_BLOB_CONTAINER", settings.STORAGE_BLOB_CONTAINER),
        )
        if not value or not value.strip()
    ]
    if missing_settings:
        error_message = f"Blob upload requires {missing_settings} to be set"
        raise UploadConfigurationError(error_message)


async def upload_results(
    results: list[PipelineResult], settings: PipelineSettings
) -> list[PipelineResult]:
    """
    Upload fetched PDFs.

    Records each upload failure without stopping others.

    Args:
        results (list[PipelineResult]): Fetch results to update.
        settings (PipelineSettings): Settings containing blob storage details.

    Returns:
        list[PipelineResult]: Results with blob names or upload failure statuses.

    Raises:
        UploadConfigurationError: If the account or container is missing.

    """
    validate_upload_settings(settings)
    if not any(result.status is Status.FETCHED for result in results):
        logger.warning("No fetched results to upload. Skipping upload step.")
        return results

    storage_client = FetchEverythingBlobStorageClient(settings)
    uploaded_results = []
    outcomes_by_path: dict[Path, tuple[Status, str | None, str | None]] = {}
    for result in results:
        if result.status is not Status.FETCHED:
            logger.info(
                f"Skipping upload for result with status {result.status} "
                f"and input DOI {result.input_doi}"
            )
            uploaded_results.append(result)
            continue
        if result.path is None:
            error_message = f"Fetched result for {result.input_doi} has no PDF path"
            logger.error(error_message)
            uploaded_results.append(
                result.model_copy(
                    update={"status": Status.UPLOAD_FAILED, "error": error_message}
                )
            )
            continue
        if result.path in outcomes_by_path:
            status, blob_name, error = outcomes_by_path[result.path]
            logger.info(
                f"Reusing upload outcome for {result.path} for {result.input_doi}"
            )
            uploaded_results.append(
                result.model_copy(
                    update={"status": status, "blob_name": blob_name, "error": error}
                )
            )
            continue

        logger.info(f"Uploading PDF for {result.input_doi} from {result.path}")
        try:
            with result.path.open("rb") as pdf_file:
                blob_name = await asyncio.to_thread(
                    storage_client.blob_upload,
                    data=pdf_file,
                    filename=result.path.name,
                )
        except (BlobUploadError, OSError) as upload_error:
            error_message = (
                f"Failed to upload {result.path} for {result.input_doi}: {upload_error}"
            )
            logger.error(error_message)
            uploaded_results.append(
                result.model_copy(
                    update={"status": Status.UPLOAD_FAILED, "error": error_message}
                )
            )
            outcomes_by_path[result.path] = (
                Status.UPLOAD_FAILED,
                result.blob_name,
                error_message,
            )
        else:
            uploaded_result = result.model_copy(update={"blob_name": blob_name})
            uploaded_results.append(uploaded_result)
            outcomes_by_path[result.path] = (
                uploaded_result.status,
                uploaded_result.blob_name,
                uploaded_result.error,
            )

    return uploaded_results


async def upload_results_manifest(
    path: Path,
    settings: PipelineSettings,
) -> str:
    """
    Upload the results manifest under a unique blob name.

    Args:
        path (Path): Local path to the completed results CSV file.
        settings (PipelineSettings): Settings containing blob storage details.

    Returns:
        str: The manifest's blob name.

    Raises:
        UploadConfigurationError: If the account or container is missing.
        ResultsManifestUploadError: If the file cannot be uploaded.

    """
    validate_upload_settings(settings)
    run_id = settings.fetch_pipeline_run_id
    if run_id is None:
        run_id = uuid4().hex
    run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip(".-_") or uuid4().hex
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    blob_name = f"results/{timestamp}-{run_id}/{path.name}"
    try:
        with path.open("rb") as results_file:
            storage_client = FetchEverythingBlobStorageClient(settings)
            uploaded_blob_name = await asyncio.to_thread(
                storage_client.blob_upload,
                data=results_file,
                filename=blob_name,
            )
    except (BlobUploadError, OSError) as upload_error:
        error_message = f"Failed to upload results manifest {path}: {upload_error}"
        logger.error(error_message)
        raise ResultsManifestUploadError(error_message) from upload_error

    logger.info(f"Uploaded results manifest to blob storage as {uploaded_blob_name}")
    return uploaded_blob_name
