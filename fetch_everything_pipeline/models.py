"""Define models for the fetch everything pipeline."""

import csv
from enum import StrEnum, auto
from pathlib import Path

from fer.config import ExternalAPI
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ResultsProcessingError(Exception):
    """Raised when a results file cannot be processed."""


class EmptyResultSetError(Exception):
    """Raised when there are no valid results to write."""


class Status(StrEnum):
    """
    Detail the outcome of processing a single input DOI.

    Attributes:
        FETCHED: A full-text file was retrieved.
        NOT_FOUND: No provider returned a full-text file.
        INVALID_DOI: The input could not be normalised to a valid DOI.
        ERROR: An unexpected error occurred while fetching.
        UPLOAD_FAILED: The file was fetched but could not be uploaded.

    """

    FETCHED = auto()
    NOT_FOUND = auto()
    INVALID_DOI = auto()
    ERROR = auto()
    UPLOAD_FAILED = auto()


class PipelineResult(BaseModel):
    """
    Result for one input DOI.

    Attributes:
        index: Position of the input in the input list.
        input_doi: The DOI string exactly as supplied.
        normalised_doi: The normalised DOI, or None if the input was invalid.
        status: Outcome of processing this input.
        source: External API that supplied the full text, when fetched.
        path: Path of the fetched file, when fetched. The CLI writes it
            relative to the output directory.
        blob_name: Name of the uploaded blob, when uploaded.
        error: Error message, when processing failed.

    """

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    input_doi: str
    normalised_doi: str | None = None
    status: Status
    source: ExternalAPI | None = None
    path: Path | None = None
    blob_name: str | None = None
    error: str | None = None


RESULT_FIELDS = tuple(PipelineResult.model_fields)
OPTIONAL_RESULT_FIELDS = frozenset(
    name
    for name, field in PipelineResult.model_fields.items()
    if not field.is_required()
)


def write_results(results: list[PipelineResult], path: Path) -> None:
    """
    Write results to a CSV file.

    The header row holds the field names in `RESULT_FIELDS` order, followed by
    one row per result. Missing values are written as empty cells.

    Args:
        results (list[PipelineResult]): Results to write.
        path (Path): Path to the CSV file to write.

    Raises:
        EmptyResultSetError: If there are no results.
        ResultsProcessingError: If the file can't be written.

    """
    if not results:
        error_message = f"No valid results to write to {path}"
        raise EmptyResultSetError(error_message)
    try:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
            writer.writeheader()
            writer.writerows(result.model_dump(mode="json") for result in results)
    except OSError as result_write_error:
        error_message = f"Failed to write results to {path}: {result_write_error}"
        raise ResultsProcessingError(error_message) from result_write_error


def empty_cells_to_none(row: dict[str, str]) -> dict[str, str | None]:
    """
    Turn the empty cells of optional fields in a CSV row into None.

    CSV can't tell an empty string from a missing value, so `write_results`
    writes None as an empty cell. Required fields keep their empty strings, so
    an empty `input_doi` reads back as written.

    Args:
        row (dict[str, str]): A row from `csv.DictReader`.

    Returns:
        dict[str, str | None]: The row, with None for empty optional fields.

    """
    return {
        key: None if value == "" and key in OPTIONAL_RESULT_FIELDS else value
        for key, value in row.items()
    }


def read_results(path: Path) -> list[PipelineResult]:
    """
    Read results from a CSV file written by `write_results`.

    Ignores blank lines. This uses the default field size limit
    for the csv module, which limits the maximum size of fields
    it can read to 131072 characters and raises a
    ResultsProcessingError if a field exceeds this limit.

    Args:
        path (Path): Path of the CSV file to read.

    Returns:
        list[PipelineResult]: The results, in file order.

    Raises:
        ResultsProcessingError: If the file can't be read or parsed, or a row
            isn't a valid result.

    """
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return [
                PipelineResult.model_validate(empty_cells_to_none(row))
                for row in csv.DictReader(f, strict=True)
            ]
    except (OSError, UnicodeDecodeError) as result_read_error:
        error_message = f"Failed to read results from {path}: {result_read_error}"
        raise ResultsProcessingError(error_message) from result_read_error
    except csv.Error as result_parse_error:
        error_message = f"Failed to parse results from {path}: {result_parse_error}"
        raise ResultsProcessingError(error_message) from result_parse_error
    except ValidationError as result_validation_error:
        error_message = (
            f"Failed to validate results from {path}: {result_validation_error}"
        )
        raise ResultsProcessingError(error_message) from result_validation_error
