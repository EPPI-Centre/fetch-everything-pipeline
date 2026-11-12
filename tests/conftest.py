"""Define test configuration and fixtures."""

from pathlib import Path

import pytest
from fer.config import ExternalAPI

from fetch_everything_pipeline.models import (
    PipelineResult,
    Status,
)
from fetch_everything_pipeline.settings import PipelineSettings


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
