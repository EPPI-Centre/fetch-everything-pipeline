"""Define mock functionality and shared helpers for tests."""

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from fer.fetch_fulltext import FullTextResult
from fer.fetching.core import DOIStudyCollection

FakeFetch = Callable[..., Awaitable[list[FullTextResult]]]


def mock_fetch(
    found: dict[str, str],
    fail_on: frozenset[str] = frozenset(),
    error_type: type[Exception] = OSError,
) -> FakeFetch:
    """
    Return a mock fetch function for the fetch within `get_many_fulltext_pdfs_cycling_apis`.

    This wraps the fetch function and returns the callable directly,
    so it can be used as a side effect for the mock fetcher.

    Args:
        found (dict[str, str]): Normalised DOI to the source FER would report.
        fail_on (frozenset[str]): Normalised DOIs whose chunk raises an error.
        error_type (type[Exception]): The exception raised for `fail_on`.

    Returns:
        FakeFetch: An async function that writes placeholder PDFs and
            returns results in reverse order to test independence from FER's ordering.

    """

    async def fetch(
        input_study_collection: DOIStudyCollection, output_directory: Path, **_: object
    ) -> list[FullTextResult]:
        """
        Define the core of the mock fetcher, which returns results for the given studies.

        Args:
            input_study_collection (DOIStudyCollection): The studies to fetch.
            output_directory (Path): The directory to write the fetched files to.

        Raises:
            error_type: Multiple error types handled here,
                but all raised for the whole chunk, not individual studies.
                Defaults to OSError, but can be overridden to any of the FER_FETCH_ERRORS.

        Returns:
            list[FullTextResult]: The results for the given studies, in reverse order.

        """
        results = []
        for study in input_study_collection.studies:
            doi = study.doi.identifier
            if doi in fail_on:
                error_message = f"Error on {doi}"
                raise error_type(error_message)
            source = found.get(doi)
            path = output_directory / f"{study.uid}.pdf" if source else None
            if path is not None:
                path.write_bytes(b"pdf contents")
            results.append(
                FullTextResult(
                    doi=doi,
                    uid=str(study.uid),
                    openalex_id=None,
                    fulltext_path=str(path) if path is not None else None,
                    source=source,
                )
            )
        return results[::-1]

    return fetch


def is_unique_blob_name(blob_name: str | None, path: Path) -> bool:
    """
    Check that a blob name is the PDF's stem plus a random hex suffix.

    Args:
        blob_name (str | None): The blob name to check.
        path (Path): The local path of the uploaded PDF.

    Returns:
        bool: True if `blob_name` is `<stem>-<32 hex characters>.pdf`.

    """
    pattern = rf"{re.escape(path.stem)}-[0-9a-f]{{32}}\.pdf"
    return blob_name is not None and re.fullmatch(pattern, blob_name) is not None
