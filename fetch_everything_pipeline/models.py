"""Define models for the fetch everything pipeline."""

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
        path: Local path of the fetched file, when fetched.
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


def write_results(results: list[PipelineResult], path: Path) -> None:
    """
    Write results to a JSON Lines file.

    Args:
        results (list[PipelineResult]): Results to write.
        path (Path): Path to the JSON Lines file to write.

    """
    if not results:
        error_message = f"No valid results to write to {path}"
        raise EmptyResultSetError(error_message)
    try:
        with path.open("w", encoding="utf-8") as f:
            f.writelines(result.model_dump_json() + "\n" for result in results)
    except OSError as result_write_error:
        error_message = f"Failed to write results to {path}: {result_write_error}"
        raise ResultsProcessingError(error_message) from result_write_error


def read_results(path: Path) -> list[PipelineResult]:
    """
    Read results from a JSON Lines file.

    Ignores blank lines.

    Args:
        path (Path): Path of the JSON Lines file to read.

    Returns:
        list[PipelineResult]: The results, in file order.

    """
    try:
        with path.open(encoding="utf-8") as f:
            return [
                PipelineResult.model_validate_json(line) for line in f if line.strip()
            ]
    except OSError as result_read_error:
        error_message = f"Failed to read results from {path}: {result_read_error}"
        raise ResultsProcessingError(error_message) from result_read_error
    except ValidationError as result_validation_error:
        error_message = (
            f"Failed to validate results from {path}: {result_validation_error}"
        )
        raise ResultsProcessingError(error_message) from result_validation_error
