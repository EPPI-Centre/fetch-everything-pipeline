"""Define test configuration and fixtures."""

from pathlib import Path

import pytest
from fer.config import ExternalAPI

from fetch_everything_pipeline.models import (
    PipelineResult,
    Status,
)


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
