"""Fetch full texts for input DOIs using FER's batch fetcher."""

from pathlib import Path
from uuid import UUID

import httpx2
from fer.config import ExternalAPI, external_api_priority
from fer.config import Settings as FERSettings
from fer.data_models.generic import FullTextNotFoundError, FullTextUnpackError
from fer.fetch_fulltext import (
    FullTextBatchFetcher,
    FullTextBatchFetcherError,
    FullTextResult,
)
from fer.fetching.core import DOIStudyCollection, FullTextStreamError
from fer.fetching.openalex import OpenAlexAPIError, PDFUnavailableError
from fer.local.run import prepare_processor
from fer.utils import InvalidDOIError
from loguru import logger
from pydantic import BaseModel, ConfigDict, ValidationError

from fetch_everything_pipeline.dois import prepare_dois
from fetch_everything_pipeline.models import PipelineResult, Status

DEFAULT_CHUNK_SIZE = 50

FER_FETCH_ERRORS: tuple[type[Exception], ...] = (
    httpx2.HTTPError,
    httpx2.StreamError,
    OSError,
    ValidationError,
    FullTextBatchFetcherError,
    FullTextStreamError,
    FullTextUnpackError,
    FullTextNotFoundError,
    InvalidDOIError,
    OpenAlexAPIError,
    PDFUnavailableError,
)


class OutputDirectoryError(Exception):
    """Raised when the output directory cannot be created or is not empty."""


class FetchOutcome(BaseModel):
    """
    Outcome of fetching one unique DOI, shared by every input with that DOI.

    Attributes:
        status: Outcome of the fetch.
        source: External API that supplied the full text, when fetched.
        path: Local path of the fetched file, when fetched.
        error: Error message, when the fetch failed.

    """

    model_config = ConfigDict(frozen=True)

    status: Status
    source: ExternalAPI | None = None
    path: Path | None = None
    error: str | None = None


def build_fetcher(settings: FERSettings) -> FullTextBatchFetcher:
    """
    Build the batch fetcher.

    Uses the fetcher from `fer.local.run.prepare_processor`,
    keeps only the Providers and priority.

    Credentials are handled by FER.

    The rest of the processor (the enhancement protocol and its
    storage client) is not used.

    Args:
        settings (FERSettings): Settings holding the provider credentials.

    Returns:
        FullTextBatchFetcher: A fetcher using every provider with credentials.

    Raises:
        VersionInfoNotFoundError: If FER's package metadata can't be found.

    """
    return prepare_processor(settings).fulltext_fetcher


def enabled_providers(fetcher: FullTextBatchFetcher) -> list[ExternalAPI]:
    """
    List the providers a fetcher will use in priority order.

    FER drops any provider whose required API key is missing, so this reflects
    the credentials in the settings the fetcher was built with.

    Args:
        fetcher (FullTextBatchFetcher): The fetcher, from `build_fetcher`.

    Returns:
        list[ExternalAPI]: The providers the fetcher will try.

    """
    return [
        ExternalAPI[name]
        for name in fetcher.all_api_configs[external_api_priority.name]
    ]


async def fetch_fulltexts(
    dois: list[str],
    output_dir: Path,
    fetcher: FullTextBatchFetcher,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> list[PipelineResult]:
    """
    Fetch full-text PDFs for a list of DOIs.

    Inputs are validated and deduplicated with `prepare_dois`, then fetched in
    chunks of unique DOIs.

    A known FER error in a chunk marks every input in that chunk as `error`
    without affecting other chunks.

    Args:
        dois (list[str]): The input DOIs in the order they were supplied.
        output_dir (Path): Directory for the fetched files. Created if missing;
            must be empty, so that files from an earlier run can't be reported
            as fetched.
        fetcher (FullTextBatchFetcher): The fetcher, from `build_fetcher`.
        chunk_size (int): Number of unique DOIs to pass to the fetcher at once.

    Returns:
        list[PipelineResult]: Exactly one result per input, in input order.

    Raises:
        ValueError: If `chunk_size` is less than 1.
        OutputDirectoryError: If `output_dir` can't be created or isn't empty.

    """
    if chunk_size < 1:
        error_message = f"chunk_size must be at least 1, got {chunk_size}"
        raise ValueError(error_message)
    prepare_output_dir(output_dir)

    prepared = prepare_dois(dois)
    results = list(prepared.invalid)
    studies = prepared.studies.studies

    for start in range(0, len(studies), chunk_size):
        chunk = DOIStudyCollection(studies=studies[start : start + chunk_size])
        outcomes = await fetch_chunk(fetcher, chunk, output_dir)
        for study in chunk.studies:
            outcome = outcomes[study.uid]
            results.extend(
                PipelineResult(
                    index=index,
                    input_doi=dois[index],
                    normalised_doi=study.doi.identifier,
                    **outcome.model_dump(),
                )
                for index in prepared.input_indices[study.uid]
            )

    return sorted(results, key=lambda result: result.index)


def prepare_output_dir(output_dir: Path) -> None:
    """
    Create the output directory if needed, and check that it is empty.

    FER skips any DOI whose `{uid}.pdf` already exists and reports it with
    `settings.robot_title` as the source, so a reused directory would hide
    which API supplied the file.

    Args:
        output_dir (Path): The directory to prepare.

    Raises:
        OutputDirectoryError: If the directory can't be created or isn't empty.

    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        is_empty = not any(output_dir.iterdir())
    except OSError as output_dir_error:
        error_message = f"Cannot use output directory {output_dir}: {output_dir_error}"
        raise OutputDirectoryError(error_message) from output_dir_error
    if not is_empty:
        error_message = f"Output directory {output_dir} is not empty"
        raise OutputDirectoryError(error_message)


async def fetch_chunk(
    fetcher: FullTextBatchFetcher,
    chunk: DOIStudyCollection,
    output_dir: Path,
) -> dict[UUID, FetchOutcome]:
    """
    Fetch one chunk of unique studies and map FER's results back by UID.

    FER only catches `FullTextFetcherError` for each provider; any other
    exception aborts its whole call. The known ones (`FER_FETCH_ERRORS`) are
    caught here and reported as an `error` outcome for every study in the
    chunk. Any other exception is a bug and propagates.

    Args:
        fetcher (FullTextBatchFetcher): The fetcher to call.
        chunk (DOIStudyCollection): The studies to fetch.
        output_dir (Path): Directory for the fetched files.

    Returns:
        dict[UUID, FetchOutcome]: An outcome for every study in the chunk.

    """
    try:
        fer_results = await fetcher.get_many_fulltext_pdfs_cycling_apis(
            input_study_collection=chunk,
            output_directory=output_dir,
        )
    except FER_FETCH_ERRORS as chunk_error:
        logger.exception("Fetching a chunk of {} DOIs failed", len(chunk.studies))
        outcome = FetchOutcome(
            status=Status.ERROR, error=f"{type(chunk_error).__name__}: {chunk_error}"
        )
        return {study.uid: outcome for study in chunk.studies}

    by_uid = {UUID(result.uid): result for result in fer_results}
    return {study.uid: to_outcome(by_uid.get(study.uid)) for study in chunk.studies}


def to_outcome(fer_result: FullTextResult | None) -> FetchOutcome:
    """
    Convert one of FER's results into an outcome.

    FER reports the source as the provider's `ExternalAPI` member name (for
    example `OPENALEX`), because that is how `prepare_api_config` keys the
    providers. Any other source, such as `settings.robot_title` for a file that
    was already on disk, is reported as an error.

    Args:
        fer_result (FullTextResult | None): FER's result for a study, or None
            if FER returned no result for it.

    Returns:
        FetchOutcome: The outcome for that study.

    """
    if fer_result is None:
        return FetchOutcome(status=Status.ERROR, error="FER returned no result")
    if fer_result.fulltext_path is None:
        return FetchOutcome(status=Status.NOT_FOUND)
    if fer_result.source not in ExternalAPI.__members__:
        return FetchOutcome(
            status=Status.ERROR,
            path=Path(fer_result.fulltext_path),
            error=f"Unexpected source {fer_result.source!r}",
        )
    return FetchOutcome(
        status=Status.FETCHED,
        source=ExternalAPI[fer_result.source],
        path=Path(fer_result.fulltext_path),
    )
