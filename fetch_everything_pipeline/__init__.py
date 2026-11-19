"""Fetch Everything Pipeline package."""

import argparse
import asyncio
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from fer.utils import VersionInfoNotFoundError
from loguru import logger
from pydantic import ValidationError

from fetch_everything_pipeline.fetch import (
    DEFAULT_CHUNK_SIZE,
    OutputDirectoryError,
    build_fetcher,
    enabled_providers,
    fetch_fulltexts,
    prepare_output_dir,
)
from fetch_everything_pipeline.models import (
    PipelineResult,
    ResultsProcessingError,
    Status,
    write_results,
)
from fetch_everything_pipeline.settings import SettingsLoadError, load_settings
from fetch_everything_pipeline.upload import (
    ResultsManifestUploadError,
    UploadConfigurationError,
    upload_results,
    upload_results_manifest,
    validate_upload_settings,
)

PDF_DIR_NAME = "pdfs"
RESULTS_FILE_NAME = "results.csv"


class InputFileError(Exception):
    """Raised when the input file cannot be read or holds no DOIs."""


class PDFPathError(Exception):
    """Raised when a fetched PDF is not inside the output directory."""


MAIN_ERRORS: tuple[type[Exception], ...] = (
    InputFileError,
    PDFPathError,
    SettingsLoadError,
    ValidationError,
    VersionInfoNotFoundError,
    OutputDirectoryError,
    ResultsProcessingError,
    UploadConfigurationError,
    ResultsManifestUploadError,
)


def read_dois(path: Path) -> list[str]:
    """
    Read DOIs from a text file with one DOI per line.

    Surrounding whitespace is removed and blank lines are skipped.
    A leading byte order mark is ignored if present (for Windows users).

    Args:
        path (Path): Path of the input file.

    Returns:
        list[str]: The DOIs in the order presented in the file.
            Duplicates and invalid lines (non-null) are preserved.

    Raises:
        InputFileError: If the file can't be read or holds no DOIs.

    """
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as read_error:
        error_message = f"Cannot read input file {path}: {read_error}"
        raise InputFileError(error_message) from read_error
    dois = [line.strip() for line in lines if line.strip()]
    if not dois:
        error_message = f"No DOIs found in input file {path}"
        raise InputFileError(error_message)
    return dois


def relative_pdf_path(result: PipelineResult, output_dir: Path) -> PipelineResult:
    """
    Make a result's PDF path relative to the output directory.

    A relative `output_dir` and an absolute PDF path compare correctly.

    Args:
        result (PipelineResult): A result whose path, if any, is inside
            `output_dir`.
        output_dir (Path): The output directory the path is made relative to.

    Returns:
        PipelineResult: The result with a relative path, or unchanged if it
            has no path.

    Raises:
        PDFPathError: If the PDF is not inside `output_dir`.

    """
    if result.path is None:
        return result
    try:
        relative_path = result.path.resolve().relative_to(output_dir.resolve())
    except ValueError as path_error:
        error_message = (
            f"PDF {result.path} for {result.input_doi} is not inside the output "
            f"directory {output_dir}"
        )
        raise PDFPathError(error_message) from path_error
    return result.model_copy(update={"path": relative_path})


def log_summary(results: list[PipelineResult]) -> None:
    """
    Log the number of results with each status.

    Args:
        results (list[PipelineResult]): The results to summarise.

    """
    counts = Counter(result.status for result in results)
    logger.info(f"Processed {len(results)} inputs")
    for status in Status:
        logger.info(f"{status}: {counts[status]}")


def parse_args(input_args: Sequence[str] | None) -> argparse.Namespace:
    """
    Parse the command-line arguments.

    Args:
        input_args (Sequence[str] | None): The arguments,
            or None to use command line arguments.

    Returns:
        argparse.Namespace: The parsed `input`, `output`, `chunk_size` and `upload`.

    Raises:
        SystemExit: If the arguments are invalid, or `--help` is given.

    """
    parser = argparse.ArgumentParser(
        prog="fetch-pipeline",
        description="Fetch full-text PDFs for a file of DOIs, one per line.",
    )
    parser.add_argument("--input", type=Path, required=True, help="DOI file.")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            f"Fresh output directory; PDFs go in {PDF_DIR_NAME}/ and results in "
            f"{RESULTS_FILE_NAME}."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Number of unique DOIs to fetch at once.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload fetched PDFs to the configured Azure Blob Storage container.",
    )
    args = parser.parse_args(input_args)
    if args.chunk_size < 1:
        parser.error("--chunk-size must be at least 1")
    return args


def main(input_args: Sequence[str] | None = None) -> int:
    """
    Fetch full texts for a file of DOIs and write the results.

    Fetch failures for individual DOIs are recorded in the results, not
    reported through the exit code.

    Args:
        input_args (Sequence[str] | None): The arguments,
            or None to use command line arguments.

    Returns:
        int: 0 on success, or 1 on a configuration, input or output error.

    Raises:
        SystemExit: If the arguments are invalid, or `--help` is given.

    """
    args = parse_args(input_args)
    try:
        settings = load_settings()
        if args.upload:
            validate_upload_settings(settings)
        dois = read_dois(args.input)
        fetcher = build_fetcher(settings)
        prepare_output_dir(args.output)
        enabled_providers_string = ", ".join(
            provider.name for provider in enabled_providers(fetcher)
        )
        logger.info(f"Enabled providers: {enabled_providers_string}")
        results = asyncio.run(
            fetch_fulltexts(dois, args.output / PDF_DIR_NAME, fetcher, args.chunk_size)
        )
        if args.upload:
            results = asyncio.run(upload_results(results, settings))
        results = [relative_pdf_path(result, args.output) for result in results]
        results_path = args.output / RESULTS_FILE_NAME
        write_results(results, results_path)
        if args.upload:
            asyncio.run(upload_results_manifest(results_path, settings))
    except MAIN_ERRORS as main_error:
        logger.error(f"Error: {main_error}")
        return 1
    log_summary(results)
    return 0
