"""Define mock functionality for testing the core fetcher and Key Vault settings."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from fer.fetch_fulltext import FullTextResult
from fer.fetching.core import DOIStudyCollection
from pydantic import BaseModel, ConfigDict
from pytest_mock import MockType

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
        fail_on (frozenset[str]): Normalised DOIs whose chunk raises and error.
        error_type (type[Exception]): The exception raised for `fail_on`.

    Returns:
        FakeFetch: An async function that writes placeholder PDFs and returns
            results in reverse order to test independence from FER's ordering.

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


class FakeSecretProperties(BaseModel):
    """
    The parts of Key Vault's `SecretProperties` that pydantic-settings reads.

    Attributes:
        name: The secret's name, e.g. `openalex-key`.
        enabled: Whether the secret is enabled; disabled secrets are skipped.

    """

    model_config = ConfigDict(frozen=True)

    name: str
    enabled: bool = True


class FakeSecret(BaseModel):
    """
    The part of Key Vault's `KeyVaultSecret` that pydantic-settings reads.

    Attributes:
        value: The secret's value.

    """

    model_config = ConfigDict(frozen=True)

    value: str


def fill_vault(secret_client: MockType, secrets: dict[str, str]) -> None:
    """
    Make a mocked `SecretClient` serve `secrets`, keyed by secret name.

    pydantic-settings lists the vault's secrets, reading each one's `name` and
    `enabled`, then fetches the value of each one it needs with `get_secret`.

    Args:
        secret_client (MockType): The patched `SecretClient` class.
        secrets (dict[str, str]): Secret names and their values.

    """
    client = secret_client.return_value
    client.list_properties_of_secrets.return_value = [
        FakeSecretProperties(name=name) for name in secrets
    ]
    client.get_secret.side_effect = lambda name: FakeSecret(value=secrets[name])
