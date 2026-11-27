"""Fetch full texts for input DOIs using FER's batch fetcher."""

import copy
from json import JSONDecodeError
from pathlib import Path
from uuid import UUID

import httpx2
from fer.config import ExternalAPI, external_api_priority
from fer.fetch_fulltext import (
    FullTextBatchFetcher,
    FullTextFetcherError,
    FullTextResult,
    RetrievedFullText,
)
from fer.fetching.core import DOIStudyCollection
from fer.local.run import prepare_processor
from loguru import logger
from pydantic import BaseModel, ConfigDict, ValidationError

from fetch_everything_pipeline.dois import prepare_dois
from fetch_everything_pipeline.models import PipelineResult, Status
from fetch_everything_pipeline.settings import PipelineSettings

DEFAULT_CHUNK_SIZE = 50

# Errors that can escape FER's `get_many_fulltext_pdfs_cycling_apis` at the
# pinned commit.
# To be reassessed when FER is updated.
FER_FETCH_ERRORS: tuple[type[Exception], ...] = (
    httpx2.HTTPError,
    OSError,
    ValidationError,
    JSONDecodeError,
    UnicodeDecodeError,
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


def build_fetcher(settings: PipelineSettings) -> FullTextBatchFetcher:
    """
    Build the batch fetcher.

    Uses the fetcher from `fer.local.run.prepare_processor`,
    keeps only the Providers and priority.

    Credentials are handled by FER. Unless `settings.use_scopus` is set,
    Scopus is passed to FER's `exclude_api`, so its key and inst token are
    never used.

    The rest of the processor (the enhancement protocol and its
    storage client) is not used.

    Args:
        settings (PipelineSettings): Settings holding the provider credentials.

    Returns:
        FullTextBatchFetcher: A fetcher using every provider with credentials.

    Raises:
        VersionInfoNotFoundError: If FER's package metadata can't be found.

    """
    exclude_api = None if settings.use_scopus else [ExternalAPI.SCOPUS]
    return prepare_processor(settings, exclude_api).fulltext_fetcher


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
        output_dir (Path): Directory for the fetched files. Created if missing and
            must be empty, so that files from an earlier run can't be reported
            as fetched. Each call needs a directory to itself: concurrent
            calls or processes (e.g. the ranks of a distributed Azure ML job)
            must use separate directories.
        fetcher (FullTextBatchFetcher): The fetcher, from `build_fetcher`.
        chunk_size (int): Number of unique DOIs to pass to the fetcher at once.

    Returns:
        list[PipelineResult]: Exactly one result per input, in input order.

    Raises:
        ValueError: If `chunk_size` is less than 1.
        OutputDirectoryError: If `output_dir` can't be created or is not empty.

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
        is_empty = next(output_dir.iterdir(), None) is None
    except OSError as output_dir_error:
        error_message = f"Cannot use output directory {output_dir}: {output_dir_error}"
        raise OutputDirectoryError(error_message) from output_dir_error
    if not is_empty:
        error_message = f"Output directory {output_dir} is not empty"
        raise OutputDirectoryError(error_message)


def recording_fetcher(
    fetcher: FullTextBatchFetcher, failed_providers: set[str]
) -> FullTextBatchFetcher:
    """
    Make a per-call shallow copy of a fetcher that records which providers fail.

    FER catches `FullTextFetcherError` for each provider and moves on, so the
    failures are only visible inside its provider loop.

    The copy's `full_text_fetcher.fetch` records them before re-raising.

    Args:
        fetcher (FullTextBatchFetcher): The shared fetcher, from `build_fetcher`.
        failed_providers (set[str]): Set of failed provider names.

    Returns:
        FullTextBatchFetcher: A copy of `fetcher` for one call.

    """
    provider_fetch = fetcher.full_text_fetcher.fetch

    async def recording_fetch(**kwargs: object) -> list[RetrievedFullText]:
        """
        Record any failed providers during the fetch.

        Args:
            **kwargs (object): The arguments FER passes to
                `FullTextFetcher.fetch`.

        Returns:
            list[RetrievedFullText]: The list of successfully retrieved full texts.

        Raises:
            FullTextFetcherError: If the provider fails, after recording it.

        """
        try:
            return await provider_fetch(**kwargs)
        except FullTextFetcherError:
            failed_providers.add(str(kwargs.get("publisher_name")))
            raise

    run_fetcher = copy.copy(fetcher)
    run_fetcher.full_text_fetcher = copy.copy(fetcher.full_text_fetcher)
    run_fetcher.full_text_fetcher.fetch = recording_fetch
    return run_fetcher


async def fetch_chunk(
    fetcher: FullTextBatchFetcher,
    chunk: DOIStudyCollection,
    output_dir: Path,
) -> dict[UUID, FetchOutcome]:
    """
    Fetch a single chunk of unique Studies and map FER's results back by UID.

    FER only catches `FullTextFetcherError` for each provider; any other
    exception aborts its whole call.
    Known errors in `FER_FETCH_ERRORS` are caught here and reported as an `error`
    outcome for every study in the chunk.

    Any other exception is a bug and propagates.

    Args:
        fetcher (FullTextBatchFetcher): The fetcher to call.
        chunk (DOIStudyCollection): The studies to fetch.
        output_dir (Path): Directory for the fetched files.

    Returns:
        dict[UUID, FetchOutcome]: An outcome for every study in the chunk.

    """
    failed_providers: set[str] = set()
    run_fetcher = recording_fetcher(fetcher, failed_providers)
    try:
        fer_results = await run_fetcher.get_many_fulltext_pdfs_cycling_apis(
            input_study_collection=chunk,
            output_directory=output_dir,
        )
    except FER_FETCH_ERRORS as chunk_error:
        logger.exception(f"Fetching a chunk of {len(chunk.studies)} DOIs failed")
        outcome = FetchOutcome(
            status=Status.ERROR, error=f"{type(chunk_error).__name__}: {chunk_error}"
        )
        return {study.uid: outcome for study in chunk.studies}

    if failed_providers.issuperset(fetcher.all_api_configs["fulltext"]):
        error_message = "All fulltext providers failed"
        logger.error(error_message)
        outcome = FetchOutcome(status=Status.ERROR, error=error_message)
        return {study.uid: outcome for study in chunk.studies}

    if failed_providers:
        warning_message = (
            f"Some fulltext providers failed: {', '.join(failed_providers)}"
        )
        logger.warning(warning_message)

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
