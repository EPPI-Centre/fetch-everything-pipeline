"""Define test configuration and fixtures."""

from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

import pytest
from fer.blob_storage import FetchEverythingBlobStorageClient
from fer.config import ExternalAPI
from fer.fetch_fulltext import FullTextBatchFetcher
from loguru import logger
from pytest_mock import MockerFixture, MockType

from fetch_everything_pipeline.fetch import build_fetcher
from fetch_everything_pipeline.models import (
    PipelineResult,
    Status,
)
from fetch_everything_pipeline.settings import PipelineSettings
from tests.fakes import mock_fetch


@pytest.fixture
def uploaded_files() -> list[tuple[bytes, str]]:
    """Record the bytes and blob names submitted by the upload mock."""
    return []


@pytest.fixture
def mock_blob_client(
    mocker: MockerFixture, uploaded_files: list[tuple[bytes, str]]
) -> MockType:
    """Patch the blob client constructor and record uploads without network I/O."""

    def record_upload(data: BinaryIO, filename: str) -> str:
        uploaded_files.append((data.read(), filename))
        return filename

    client_factory = mocker.patch(
        "fetch_everything_pipeline.upload.FetchEverythingBlobStorageClient",
        autospec=FetchEverythingBlobStorageClient,
    )
    client_factory.return_value.blob_upload.side_effect = record_upload
    return client_factory


@pytest.fixture
def caplog(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """
    Make pytest's `caplog` capture loguru messages.

    Loguru doesn't use the standard `logging` module, so this adds a sink that
    writes to `caplog.handler` for the duration of the test.

    Args:
        caplog (pytest.LogCaptureFixture): pytest's built-in fixture.

    Yields:
        pytest.LogCaptureFixture: The same fixture, now receiving loguru output.

    """
    handler_id = logger.add(
        caplog.handler,
        format="{message}",
        level=0,
        filter=lambda record: record["level"].no >= caplog.handler.level,
        enqueue=False,
    )
    yield caplog
    logger.remove(handler_id)


@pytest.fixture
def test_fetcher(
    mocker: MockerFixture, test_settings: PipelineSettings
) -> FullTextBatchFetcher:
    """
    Mock a `FullTextBatchFetcher` that finds nothing by default.

    Args:
        mocker (MockerFixture): The pytest-mock fixture.
        test_settings (PipelineSettings): Settings the real fetcher is built
            from.

    Returns:
        FullTextBatchFetcher: The mock with the side effect of
            `get_many_fulltext_pdfs_cycling_apis` set to `mock_fetch` to change
            what it finds.

    """
    mock = mocker.create_autospec(FullTextBatchFetcher, instance=True)
    mock.all_api_configs = build_fetcher(test_settings).all_api_configs
    mock.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch({})
    return mock


@pytest.fixture
def cli_fetcher_with_pdf(
    mocker: MockerFixture, test_fetcher: FullTextBatchFetcher
) -> None:
    """Make the CLI fetch one PDF without contacting a provider."""
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {"10.1000/a": "OPENALEX"}
    )
    mocker.patch("fetch_everything_pipeline.build_fetcher", return_value=test_fetcher)


@pytest.fixture
def test_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineSettings:
    """
    Build settings from a known environment with only MAILTO and OPENALEX_KEY set.

    Runs in an empty temporary directory so no `.env` file is read, and clears
    the other settings variables so the developer's shell can't leak in.

    Returns:
        PipelineSettings: Settings loaded from the patched environment.

    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAILTO", "pipeline@example.org")
    monkeypatch.setenv("OPENALEX_KEY", "openalex-test-key")
    monkeypatch.delenv("ELSEVIER_SCOPUS_KEY", raising=False)
    monkeypatch.delenv("ELSEVIER_SCOPUS_INST_TOKEN", raising=False)
    monkeypatch.delenv("SCOPUS_PROXY_URL", raising=False)
    monkeypatch.delenv("DESTINY_REPOSITORY_URL", raising=False)
    monkeypatch.delenv("STORAGE_BLOB_ACCOUNT", raising=False)
    monkeypatch.delenv("STORAGE_BLOB_CONTAINER", raising=False)
    monkeypatch.delenv("STORAGE_BLOB_ACCOUNT_KEY", raising=False)
    return PipelineSettings()


@pytest.fixture
def results() -> list[PipelineResult]:
    return [
        PipelineResult(
            index=0,
            input_doi="https://doi.org/10.1000/ABC",
            normalised_doi="10.1000/abc",
            status=Status.FETCHED,
            source=ExternalAPI.OPENALEX,
            path=Path("out/pdfs/abc.pdf"),
        ),
        PipelineResult(index=1, input_doi="not a doi", status=Status.INVALID_DOI),
        PipelineResult(
            index=2,
            input_doi="10.1000/xyz",
            normalised_doi="10.1000/xyz",
            status=Status.ERROR,
            error="boom",
        ),
        PipelineResult(
            index=3,
            input_doi="10.1000/abc",
            normalised_doi="10.1000/abc",
            status=Status.UPLOAD_FAILED,
            source=ExternalAPI.OPENALEX,
            path=Path("out/pdfs/abc.pdf"),
            error="403",
        ),
    ]
