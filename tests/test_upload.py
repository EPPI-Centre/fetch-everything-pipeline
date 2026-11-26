"""Tests for uploading fetched full texts."""

import re
from pathlib import Path

import pytest
from fer.blob_storage import BlobUploadError
from fer.config import ExternalAPI
from pytest_mock import MockType

from fetch_everything_pipeline.models import PipelineResult, Status
from fetch_everything_pipeline.settings import PipelineSettings
from fetch_everything_pipeline.upload import (
    ResultsManifestUploadError,
    UploadConfigurationError,
    upload_results,
    upload_results_manifest,
    validate_upload_settings,
)
from tests.fakes import is_unique_blob_name


def fetched_result(index: int, path: Path) -> PipelineResult:
    return PipelineResult(
        index=index,
        input_doi=f"10.1000/{index}",
        normalised_doi=f"10.1000/{index}",
        status=Status.FETCHED,
        source=ExternalAPI.OPENALEX,
        path=path,
    )


def blob_settings(test_settings: PipelineSettings) -> PipelineSettings:
    return test_settings.model_copy(
        update={
            "STORAGE_BLOB_ACCOUNT": "testaccount",
            "STORAGE_BLOB_CONTAINER": "testcontainer",
        }
    )


def test_validate_upload_settings_success(test_settings: PipelineSettings):
    settings = blob_settings(test_settings)
    validate_upload_settings(settings)
    assert settings.STORAGE_BLOB_ACCOUNT == "testaccount"
    assert settings.STORAGE_BLOB_CONTAINER == "testcontainer"


@pytest.mark.parametrize(
    "settings_item", [{"STORAGE_BLOB_ACCOUNT": None}, {"STORAGE_BLOB_CONTAINER": None}]
)
def test_validate_upload_settings_failure(
    test_settings: PipelineSettings, settings_item
):
    settings = test_settings.model_copy(update=settings_item)
    with pytest.raises(UploadConfigurationError):
        validate_upload_settings(settings)


async def test_upload_results_uploads_fetched_files_and_preserves_duplicates(
    mock_blob_client: MockType,
    uploaded_files: list[tuple[bytes, str]],
    test_settings: PipelineSettings,
    tmp_path: Path,
):
    pdf_filename = "paper.pdf"
    pdf_path = tmp_path / pdf_filename
    pdf_content = b"pdf contents"
    pdf_path.write_bytes(pdf_content)
    fetched = fetched_result(0, pdf_path)
    duplicate = fetched.model_copy(update={"index": 2, "input_doi": "doi:10.1000/0"})
    not_found = PipelineResult(index=1, input_doi="10.1000/1", status=Status.NOT_FOUND)
    results = await upload_results(
        [fetched, not_found, duplicate], blob_settings(test_settings)
    )

    assert [result.status for result in results] == [
        Status.FETCHED,
        Status.NOT_FOUND,
        Status.FETCHED,
    ]
    assert is_unique_blob_name(
        results[0].blob_name, pdf_path
    ), "Expected the first result's blob name to be the PDF stem plus a unique suffix"
    assert (
        results[1] is not_found
    ), "Expected the second result to be the not found result"
    assert (
        results[2].blob_name == results[0].blob_name
    ), "Expected the duplicate result to share the first result's blob name"
    assert uploaded_files == [(pdf_content, results[0].blob_name)]
    assert (
        mock_blob_client.return_value.blob_upload.call_count == 1
    ), "Expected one upload: the duplicate should reuse the first upload"


async def test_upload_results_single_failure_does_not_stop_later_uploads(
    mock_blob_client: MockType,
    test_settings: PipelineSettings,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    first_path = tmp_path / "first.pdf"
    second_path = tmp_path / "second.pdf"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    blob_upload = mock_blob_client.return_value.blob_upload
    blob_upload.side_effect = [
        BlobUploadError("permission denied"),
        "second.pdf",
    ]

    with caplog.at_level("INFO"):
        results = await upload_results(
            [fetched_result(0, first_path), fetched_result(1, second_path)],
            blob_settings(test_settings),
        )

    assert (
        "Uploading PDF for" in caplog.text
    ), "Expected a log message indicating an upload attempt"

    assert [result.status for result in results] == [
        Status.UPLOAD_FAILED,
        Status.FETCHED,
    ]
    assert results[0].error is not None, "Expected the first result to have an error"
    assert (
        "permission denied" in results[0].error
    ), "Expected the error message to contain 'permission denied'"
    assert (
        results[1].blob_name == "second.pdf"
    ), "Expected the second result to record the blob name the client returned"
    assert is_unique_blob_name(
        blob_upload.call_args.kwargs["filename"], second_path
    ), "Expected the second upload to use the PDF stem plus a unique suffix"
    assert (
        blob_upload.call_count == 2
    ), "Expected the blob upload to be called twice, once per fetched result"


async def test_upload_results_uses_new_blob_name_for_each_run(
    mock_blob_client: MockType,
    uploaded_files: list[tuple[bytes, str]],
    test_settings: PipelineSettings,
    tmp_path: Path,
):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"pdf contents")
    settings = blob_settings(test_settings)

    [first_run] = await upload_results([fetched_result(0, pdf_path)], settings)
    [second_run] = await upload_results([fetched_result(0, pdf_path)], settings)

    assert is_unique_blob_name(first_run.blob_name, pdf_path)
    assert is_unique_blob_name(second_run.blob_name, pdf_path)
    assert (
        first_run.blob_name != second_run.blob_name
    ), "Expected a later run not to reuse, and so overwrite, an earlier run's blob"
    assert [blob_name for _, blob_name in uploaded_files] == [
        first_run.blob_name,
        second_run.blob_name,
    ]


async def test_upload_results_all_failures(
    mock_blob_client: MockType,
    test_settings: PipelineSettings,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    first_path = tmp_path / "first.pdf"
    second_path = tmp_path / "second.pdf"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    blob_upload = mock_blob_client.return_value.blob_upload
    blob_upload.side_effect = [
        BlobUploadError("permission denied"),
        BlobUploadError("network error"),
    ]
    test_pipeline_results = [
        fetched_result(0, first_path),
        fetched_result(1, second_path),
    ]
    with caplog.at_level("INFO"):
        results = await upload_results(
            test_pipeline_results,
            blob_settings(test_settings),
        )

    assert [result.status for result in results] == [
        Status.UPLOAD_FAILED,
        Status.UPLOAD_FAILED,
    ]
    first_error, second_error = (result.error for result in results)
    assert first_error is not None, "Expected the first result to have an error"
    assert second_error is not None, "Expected the second result to have an error"
    assert (
        "permission denied" in first_error
    ), "Expected the first error message to contain 'permission denied'"
    assert (
        "network error" in second_error
    ), "Expected the second error message to contain 'network error'"
    assert blob_upload.call_count == len(
        test_pipeline_results
    ), "Expected the blob upload to be called twice, once per fetched result"


async def test_upload_results_client_creation_failure_marks_fetched_upload_failed(
    mock_blob_client: MockType,
    test_settings: PipelineSettings,
    tmp_path: Path,
):
    mock_blob_client.side_effect = BlobUploadError("no credential")
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"pdf contents")
    fetched = fetched_result(0, pdf_path)
    not_found = PipelineResult(index=1, input_doi="10.1000/1", status=Status.NOT_FOUND)

    results = await upload_results([fetched, not_found], blob_settings(test_settings))

    assert (
        results[0].status is Status.UPLOAD_FAILED
    ), "Expected the result to be marked as a failed upload when the client cannot be created"
    assert results[0].error is not None, "Expected the result to have an error message"
    assert (
        "no credential" in results[0].error
    ), "Expected the error to include the client creation failure"
    assert results[1] is not_found, "Expected non-fetched results to be unchanged"
    mock_blob_client.return_value.blob_upload.assert_not_called()


async def test_upload_results_skips_non_fetched_results(
    mock_blob_client: MockType,
    test_settings: PipelineSettings,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
):
    not_fetched = PipelineResult(
        index=0,
        input_doi="10.1000/0",
        normalised_doi="10.1000/0",
        status=Status.NOT_FOUND,
        source=ExternalAPI.OPENALEX,
    )
    fetched = fetched_result(1, tmp_path / "second.pdf")

    with caplog.at_level("INFO"):
        await upload_results([fetched, not_fetched], blob_settings(test_settings))
    assert (
        "Skipping upload for result" in caplog.text
    ), "Expected a log message indicating a skipped upload"
    mock_blob_client.assert_called_once()


async def test_upload_results_reuses_upload_outcome_for_same_path_result(
    mock_blob_client: MockType,
    test_settings: PipelineSettings,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    first_path = tmp_path / "first.pdf"
    first_path.write_text("content", encoding="utf-8")
    second_path = tmp_path / "first.pdf"
    second_path.write_text("content", encoding="utf-8")

    first_result = fetched_result(0, first_path)
    second_result = fetched_result(1, second_path)

    mock_blob_client.return_value.blob_upload.reset_mock()
    with caplog.at_level("INFO"):
        results = await upload_results(
            [first_result, second_result],
            blob_settings(test_settings),
        )

    assert (
        "Reusing upload outcome for" in caplog.text
    ), "Expected a log message indicating reuse of upload outcome"
    assert (
        "Uploading PDF for" in caplog.text
    ), "Expected a log message indicating an upload attempt"
    assert [result.status for result in results] == [
        first_result.status,
        second_result.status,
    ]
    assert is_unique_blob_name(
        results[0].blob_name, first_path
    ), "Expected the first result's blob name to be the PDF stem plus a unique suffix"
    assert (
        results[1].blob_name == results[0].blob_name
    ), "Expected the same-path result to share the first upload's blob name"
    assert (
        mock_blob_client.return_value.blob_upload.call_count == 1
    ), "Expected the blob upload to be called once, as the second result reuses the first upload"


@pytest.mark.parametrize(
    ("update", "missing_setting"),
    [
        ({}, "STORAGE_BLOB_ACCOUNT"),
        ({"STORAGE_BLOB_ACCOUNT": "testaccount"}, "STORAGE_BLOB_CONTAINER"),
    ],
)
async def test_upload_requires_account_and_container(
    mock_blob_client: MockType,
    test_settings: PipelineSettings,
    update: dict[str, str],
    missing_setting: str,
):
    with pytest.raises(UploadConfigurationError, match=missing_setting):
        await upload_results([], test_settings.model_copy(update=update))

    mock_blob_client.assert_not_called()


async def test_upload_results_missing_pdf_path_marks_result_upload_failed(
    mock_blob_client: MockType, test_settings: PipelineSettings
):
    result = PipelineResult(
        index=0,
        input_doi="10.1000/0",
        normalised_doi="10.1000/0",
        status=Status.FETCHED,
        source=ExternalAPI.OPENALEX,
    )

    [uploaded_result] = await upload_results([result], blob_settings(test_settings))

    assert uploaded_result.status is Status.UPLOAD_FAILED
    assert uploaded_result.error is not None
    assert "no PDF path" in uploaded_result.error
    mock_blob_client.assert_called_once()
    mock_blob_client.return_value.blob_upload.assert_not_called()


@pytest.mark.usefixtures("test_settings")
async def test_uploads_manifest_with_timestamp_and_azure_run_id(
    mock_blob_client: MockType,
    uploaded_files: list[tuple[bytes, str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    test_azure_job_name = "azure-job-123"
    test_results_file_name = "test_results.jsonl"
    test_manifest_file_content = '{"input_doi":"10.1000/a"}\n'
    manifest_path = tmp_path / test_results_file_name
    manifest_path.write_text(test_manifest_file_content, encoding="utf-8")
    monkeypatch.setenv("FETCH_PIPELINE_RUN_ID", test_azure_job_name)
    settings = blob_settings(PipelineSettings())

    with caplog.at_level("INFO"):
        first_blob = await upload_results_manifest(manifest_path, settings)

    assert (
        "Uploaded results manifest" in caplog.text
    ), "Expected log to report a successful manifest upload"
    assert first_blob.startswith(
        "results/"
    ), "Expected the blob path to start with 'results/'"
    _, timestamp_and_run_id, filename = first_blob.split("/")
    assert re.fullmatch(
        rf"\d{{8}}T\d{{6}}Z-{test_azure_job_name}-[0-9a-f]{{32}}", timestamp_and_run_id
    )
    assert filename == test_results_file_name
    assert uploaded_files == [(test_manifest_file_content.encode("utf-8"), first_blob)]
    mock_blob_client.assert_called_once()


@pytest.mark.usefixtures("test_settings", "mock_blob_client")
async def test_manifest_uses_azureml_run_id_or_local_uuid_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_manifest_file_name = "test_results.jsonl"
    manifest_path = tmp_path / test_manifest_file_name
    test_manifest_file_content = "{}\n"

    test_azure_job_name = "azure-job-123"
    manifest_path.write_text(test_manifest_file_content, encoding="utf-8")

    monkeypatch.delenv("FETCH_PIPELINE_RUN_ID", raising=False)
    monkeypatch.setenv("AZUREML_RUN_ID", test_azure_job_name)
    settings = blob_settings(PipelineSettings())

    azureml_blob = await upload_results_manifest(manifest_path, settings)
    monkeypatch.delenv("AZUREML_RUN_ID")
    local_settings = blob_settings(PipelineSettings())
    local_blob = await upload_results_manifest(manifest_path, local_settings)

    assert re.fullmatch(
        rf"results/\d{{8}}T\d{{6}}Z-{test_azure_job_name}-[0-9a-f]{{32}}/{test_manifest_file_name}",
        azureml_blob,
    )
    assert local_blob != azureml_blob
    assert local_blob.startswith("results/")
    assert local_blob.endswith(f"/{test_manifest_file_name}")


@pytest.mark.usefixtures("test_settings", "mock_blob_client")
async def test_manifest_names_are_unique_for_same_run_id(
    uploaded_files: list[tuple[bytes, str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest_path = tmp_path / "results.jsonl"
    manifest_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("FETCH_PIPELINE_RUN_ID", "shared-run-id")
    settings = blob_settings(PipelineSettings())

    first_blob = await upload_results_manifest(manifest_path, settings)
    second_blob = await upload_results_manifest(manifest_path, settings)

    assert (
        first_blob != second_blob
    ), "Expected runs sharing a run ID not to overwrite each other's manifest"
    assert [blob_name for _, blob_name in uploaded_files] == [first_blob, second_blob]


@pytest.mark.parametrize("error_type", [(BlobUploadError), (OSError)])
async def test_manifest_upload_error_is_reported(
    mock_blob_client: MockType,
    test_settings: PipelineSettings,
    tmp_path: Path,
    error_type: type,
):
    manifest_path = tmp_path / "results.jsonl"
    manifest_path.write_text("{}\n", encoding="utf-8")
    mock_blob_client.return_value.blob_upload.side_effect = error_type("test_error")

    with pytest.raises(ResultsManifestUploadError, match="test_error"):
        await upload_results_manifest(manifest_path, blob_settings(test_settings))
