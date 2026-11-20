"""Tests for the pipeline result contract."""

import csv
from pathlib import Path

import pytest
from fer.config import ExternalAPI
from pydantic import ValidationError

from fetch_everything_pipeline.models import (
    EmptyResultSetError,
    PipelineResult,
    ResultsProcessingError,
    Status,
    empty_cells_to_none,
    read_results,
    write_results,
)


def read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def test_round_trip_happy_path(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.csv"
    write_results(results, path)
    assert read_results(path) == results


def test_header_then_one_row_per_result_in_order(
    tmp_path: Path, results: list[PipelineResult]
):
    path = tmp_path / "results.csv"
    write_results(results, path)
    header, *rows = read_rows(path)
    assert header == list(PipelineResult.model_fields)
    assert [row[0] for row in rows] == ["0", "1", "2", "3"]


def test_serialised_shape(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.csv"
    write_results(results[:2], path)

    with path.open(encoding="utf-8", newline="") as f:
        first, second = csv.DictReader(f)
    assert first == {
        "index": "0",
        "input_doi": "https://doi.org/10.1000/ABC",
        "normalised_doi": "10.1000/abc",
        "status": Status.FETCHED.value,
        "source": ExternalAPI.OPENALEX.value,
        "path": str(Path("out/pdfs/abc.pdf")),
        "blob_name": "",
        "error": "",
    }
    assert second == {
        "index": "1",
        "input_doi": "not a doi",
        "normalised_doi": "",
        "status": Status.INVALID_DOI.value,
        "source": "",
        "path": "",
        "blob_name": "",
        "error": "",
    }


@pytest.mark.parametrize(
    "error",
    [
        "HTTPError: 503, retry later",
        'Unexpected source "robot"',
        "line one\nline two",
    ],
)
def test_round_trip_csv_handles_special_characters(tmp_path: Path, error: str):
    result = PipelineResult(
        index=0,
        input_doi="10.1000/a,b",
        normalised_doi="10.1000/a,b",
        status=Status.ERROR,
        error=error,
    )
    path = tmp_path / "results.csv"
    write_results([result], path)
    assert read_results(path) == [result]


def test_round_trip_keeps_empty_input_doi(tmp_path: Path):
    result = PipelineResult(index=0, input_doi="", status=Status.INVALID_DOI)
    path = tmp_path / "results.csv"
    write_results([result], path)
    [read_back] = read_results(path)
    assert read_back.input_doi == ""
    assert read_back == result


def test_empty_cells_to_none_only_changes_empty_optional_fields():
    row = {
        "index": "0",
        "input_doi": "",
        "status": "invalid_doi",
        "source": "",
        "error": "test_error",
    }
    assert empty_cells_to_none(row) == {
        "index": "0",
        "input_doi": "",
        "status": "invalid_doi",
        "source": None,
        "error": "test_error",
    }


def test_write_overwrites_existing_file(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.csv"
    write_results(results, path)
    write_results(results[:1], path)
    assert read_results(path) == results[:1]


def test_empty_results(tmp_path: Path):
    path = tmp_path / "results.csv"
    with pytest.raises(EmptyResultSetError):
        write_results([], path)


def test_write_reports_unwritable_path(tmp_path: Path, results: list[PipelineResult]):
    with pytest.raises(ResultsProcessingError, match="Failed to write results"):
        write_results(results, tmp_path / "missing" / "results.csv")


def test_read_skips_blank_lines(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.csv"
    write_results(results[:1], path)
    header, row = path.read_text(encoding="utf-8").splitlines()
    path.write_text(f"{header}\n\n{row}\n\n", encoding="utf-8")
    assert read_results(path) == results[:1]


def test_read_rejects_invalid_row(tmp_path: Path):
    path = tmp_path / "results.csv"
    path.write_text("index\n0\n", encoding="utf-8")
    with pytest.raises(ResultsProcessingError, match="Failed to validate"):
        read_results(path)


def test_read_rejects_unparseable_file(tmp_path: Path):
    path = tmp_path / "results.csv"
    path.write_text("index\n" + "x" * (csv.field_size_limit() + 1), encoding="utf-8")
    with pytest.raises(ResultsProcessingError, match="Failed to parse"):
        read_results(path)


def test_read_reports_missing_file(tmp_path: Path):
    with pytest.raises(ResultsProcessingError, match="Failed to read"):
        read_results(tmp_path / "missing.csv")


def test_status_values():
    assert {s.value for s in Status} == {
        "fetched",
        "not_found",
        "invalid_doi",
        "error",
        "upload_failed",
    }


def test_rejects_unknown_status():
    with pytest.raises(ValidationError):
        PipelineResult(index=0, input_doi="10.1/x", status="lost")


@pytest.mark.parametrize("source", list(ExternalAPI))
def test_accepts_every_external_api_source(source: ExternalAPI):
    result = PipelineResult(
        index=0, input_doi="10.1/x", status=Status.FETCHED, source=source.value
    )
    assert result.source is source


def test_rejects_source_outside_external_api():
    """
    Rejects any source that is not in the ExternalAPI enum.

    This is a safety check to ensure that only valid external APIs are used as sources.
    FER reports `settings.robot_title` for files already on disk
    and that must not get passed as a provider in this case.
    """
    with pytest.raises(ValidationError):
        PipelineResult(
            index=0,
            input_doi="10.1/x",
            status=Status.FETCHED,
            source="fetch-everything-robot",
        )


def test_rejects_negative_index():
    with pytest.raises(ValidationError):
        PipelineResult(index=-1, input_doi="10.1/x", status=Status.NOT_FOUND)


def test_results_are_immutable(results):
    with pytest.raises(ValidationError):
        results[0].status = Status.ERROR
