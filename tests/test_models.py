"""Tests for the pipeline result contract."""

import json
from pathlib import Path

import pytest
from fer.config import ExternalAPI
from pydantic import ValidationError

from fetch_everything_pipeline.models import (
    EmptyResultSetError,
    PipelineResult,
    ResultsProcessingError,
    Status,
    read_results,
    write_results,
)


def test_round_trip_happy_path(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.jsonl"
    write_results(results, path)
    assert read_results(path) == results


def test_one_line_per_result_in_order(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.jsonl"
    write_results(results, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["index"] for line in lines] == [0, 1, 2, 3]


def test_serialised_shape(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.jsonl"
    write_results(results[:2], path)

    expected_first = results[0].model_dump_json()
    expected_first_doi = results[0].normalised_doi

    expected_second = results[1].model_dump_json()
    expected_second_doi = results[1].normalised_doi
    first, second = (json.loads(line) for line in path.read_text().splitlines())
    assert first == json.loads(expected_first)
    assert second == json.loads(expected_second)
    assert first["status"] == Status.FETCHED.value
    assert first["source"] == ExternalAPI.OPENALEX.value
    assert first["normalised_doi"] == expected_first_doi
    assert second["status"] == Status.INVALID_DOI.value
    assert second["source"] is None
    assert second["normalised_doi"] == expected_second_doi is None


def test_write_overwrites_existing_file(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.jsonl"
    write_results(results, path)
    write_results(results[:1], path)
    assert read_results(path) == results[:1]


def test_empty_results(tmp_path: Path):
    path = tmp_path / "results.jsonl"
    with pytest.raises(EmptyResultSetError):
        write_results([], path)


def test_read_skips_blank_lines(tmp_path: Path, results: list[PipelineResult]):
    path = tmp_path / "results.jsonl"
    path.write_text("\n" + results[0].model_dump_json() + "\n\n")
    assert read_results(path) == results[:1]


def test_read_rejects_invalid_line(tmp_path: Path):
    path = tmp_path / "results.jsonl"
    path.write_text('{"index": 0}\n')
    with pytest.raises(ResultsProcessingError):
        read_results(path)


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
