"""Tests for fetch orchestration."""

from pathlib import Path
from typing import Any

import pytest
from fer.config import ExternalAPI
from fer.fetch_fulltext import FullTextBatchFetcher
from pydantic import SecretStr, ValidationError

from fetch_everything_pipeline.fetch import (
    FER_FETCH_ERRORS,
    OutputDirectoryError,
    build_fetcher,
    enabled_providers,
    fetch_fulltexts,
)
from fetch_everything_pipeline.models import Status
from fetch_everything_pipeline.settings import PipelineSettings
from tests.fakes import mock_fetch


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "pdfs"


def chunks_passed(test_fetcher: FullTextBatchFetcher) -> list[list[str]]:
    """Return the normalised DOIs passed to the fetcher in each call."""
    return [
        [
            study.doi.identifier
            for study in call.kwargs["input_study_collection"].studies
        ]
        for call in test_fetcher.get_many_fulltext_pdfs_cycling_apis.call_args_list
    ]


async def test_one_result_per_input_in_input_order(
    test_fetcher: FullTextBatchFetcher, output_dir: Path
):
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {"10.1000/a": "OPENALEX", "10.1000/c": "UNPAYWALL"}
    )
    dois = [
        "10.1000/a",
        "not a doi",
        "10.1000/b",
        "https://doi.org/10.1000/A",
        "10.1000/c",
    ]

    results = await fetch_fulltexts(dois, output_dir, test_fetcher)

    assert [r.index for r in results] == [0, 1, 2, 3, 4]
    assert [r.input_doi for r in results] == dois
    assert [r.status for r in results] == [
        Status.FETCHED,
        Status.INVALID_DOI,
        Status.NOT_FOUND,
        Status.FETCHED,
        Status.FETCHED,
    ]
    assert [r.normalised_doi for r in results] == [
        "10.1000/a",
        None,
        "10.1000/b",
        "10.1000/a",
        "10.1000/c",
    ]


@pytest.mark.parametrize("source", list(ExternalAPI))
async def test_source_passed_through_from_fer(
    test_fetcher: FullTextBatchFetcher, output_dir: Path, source: ExternalAPI
):
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {"10.1000/a": source.name}
    )

    [result] = await fetch_fulltexts(["10.1000/a"], output_dir, test_fetcher)

    assert result.status is Status.FETCHED
    assert result.source is source
    assert result.path is not None
    assert result.path.parent == output_dir


async def test_duplicates_fetched_once_and_share_result(
    test_fetcher: FullTextBatchFetcher, output_dir: Path
):
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {"10.1000/a": "CROSSREF"}
    )

    first, second = await fetch_fulltexts(
        ["10.1000/a", "doi:10.1000/A"], output_dir, test_fetcher
    )

    assert chunks_passed(test_fetcher) == [["10.1000/a"]]
    assert first.model_dump(exclude={"index", "input_doi"}) == second.model_dump(
        exclude={"index", "input_doi"}
    )


@pytest.mark.parametrize(
    "error_type",
    [error for error in FER_FETCH_ERRORS if error is not ValidationError],
)
async def test_failing_chunk_does_not_affect_other_chunks(
    test_fetcher: FullTextBatchFetcher, output_dir: Path, error_type: type[Exception]
):
    """
    Test that a chunk that raises an error doesn't prevent other chunks from being fetched.

    ValidationError is excluded because it is raised for individual inputs, not the whole chunk.
    """
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {"10.1000/a": "OPENALEX", "10.1000/c": "OPENALEX"},
        fail_on=frozenset({"10.1000/b"}),
        error_type=error_type,
    )

    results = await fetch_fulltexts(
        ["10.1000/a", "10.1000/b", "10.1000/c"], output_dir, test_fetcher, chunk_size=1
    )

    assert [r.status for r in results] == [Status.FETCHED, Status.ERROR, Status.FETCHED]
    assert results[1].error == f"{error_type.__name__}: Error on 10.1000/b"


async def test_unexpected_exception_propagates(
    test_fetcher: FullTextBatchFetcher, output_dir: Path
):
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {}, fail_on=frozenset({"10.1000/a"}), error_type=RuntimeError
    )

    with pytest.raises(RuntimeError, match="Error on 10.1000/a"):
        await fetch_fulltexts(["10.1000/a"], output_dir, test_fetcher)


async def test_failing_chunk_marks_every_input_in_it(
    test_fetcher: FullTextBatchFetcher, output_dir: Path
):
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {}, fail_on=frozenset({"10.1000/b"})
    )

    results = await fetch_fulltexts(
        ["10.1000/a", "10.1000/b", "10.1000/a"], output_dir, test_fetcher, chunk_size=2
    )

    assert [r.status for r in results] == [Status.ERROR] * 3


async def test_chunks_unique_dois(test_fetcher: FullTextBatchFetcher, output_dir: Path):
    dois = [
        "10.1000/0",
        "not a doi",
        "https://doi.org/10.1000/0",
        "10.1000/1",
        "10.1000/2",
        "doi:10.1000/1",
        "",
        "10.1000/3",
        "10.1000/A",
        "10.1000/a",
    ]

    await fetch_fulltexts(dois, output_dir, test_fetcher, chunk_size=2)

    assert chunks_passed(test_fetcher) == [
        ["10.1000/0", "10.1000/1"],
        ["10.1000/2", "10.1000/3"],
        ["10.1000/a"],
    ]


async def test_unexpected_source_is_error(
    test_fetcher: FullTextBatchFetcher, output_dir: Path
):
    """FER reports `robot_title` for files already on disk; that isn't a provider."""
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {"10.1000/a": "Fetch Everything Robot (FER)"}
    )

    [result] = await fetch_fulltexts(["10.1000/a"], output_dir, test_fetcher)

    assert result.status is Status.ERROR
    assert result.source is None
    assert result.error == "Unexpected source 'Fetch Everything Robot (FER)'"


async def test_missing_fer_result_is_error(
    test_fetcher: FullTextBatchFetcher, output_dir: Path
):
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = None
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.return_value = []

    [result] = await fetch_fulltexts(["10.1000/a"], output_dir, test_fetcher)

    assert result.status is Status.ERROR
    assert result.error == "FER returned no result"


async def test_all_invalid_skips_fetcher(
    test_fetcher: FullTextBatchFetcher, output_dir: Path
):
    results = await fetch_fulltexts(["bad", ""], output_dir, test_fetcher)

    assert [r.status for r in results] == [Status.INVALID_DOI] * 2
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.assert_not_called()


async def test_empty_input(test_fetcher: FullTextBatchFetcher, output_dir: Path):
    assert await fetch_fulltexts([], output_dir, test_fetcher) == []
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.assert_not_called()


async def test_creates_missing_output_dir(
    test_fetcher: FullTextBatchFetcher, tmp_path: Path
):
    output_dir = tmp_path / "new" / "pdfs"

    await fetch_fulltexts(["10.1000/a"], output_dir, test_fetcher)

    assert output_dir.is_dir()


async def test_rejects_non_empty_output_dir(
    test_fetcher: FullTextBatchFetcher, output_dir: Path
):
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"%PDF")

    with pytest.raises(OutputDirectoryError):
        await fetch_fulltexts(["10.1000/a"], output_dir, test_fetcher)
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.assert_not_called()


async def test_rejects_output_path_that_is_a_file(
    test_fetcher: FullTextBatchFetcher, tmp_path: Path
):
    output_dir = tmp_path / "pdfs"
    output_dir.write_text("not a directory")

    with pytest.raises(OutputDirectoryError):
        await fetch_fulltexts(["10.1000/a"], output_dir, test_fetcher)


@pytest.mark.parametrize("chunk_size", [0, -1])
async def test_rejects_invalid_chunk_size(
    test_fetcher: FullTextBatchFetcher, output_dir: Path, chunk_size: int
):
    with pytest.raises(ValueError, match="chunk_size"):
        await fetch_fulltexts(
            ["10.1000/a"], output_dir, test_fetcher, chunk_size=chunk_size
        )


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        pytest.param(
            {},
            [ExternalAPI.OPENALEX, ExternalAPI.CROSSREF, ExternalAPI.UNPAYWALL],
            id="scopus-dropped-without-key",
        ),
        pytest.param(
            {"openalex_key": None},
            [ExternalAPI.CROSSREF, ExternalAPI.UNPAYWALL],
            id="openalex-and-scopus-dropped-without-keys",
        ),
        pytest.param(
            {"openalex_key": SecretStr("")},
            [ExternalAPI.CROSSREF, ExternalAPI.UNPAYWALL],
            id="empty-openalex-key-counts-as-missing",
        ),
        pytest.param(
            {"elsevier_scopus_key": SecretStr("scopus-test-key"), "use_scopus": True},
            [
                ExternalAPI.OPENALEX,
                ExternalAPI.CROSSREF,
                ExternalAPI.UNPAYWALL,
                ExternalAPI.SCOPUS,
            ],
            id="all-providers-in-priority-order",
        ),
        pytest.param(
            {
                "elsevier_scopus_key": SecretStr("scopus-test-key"),
                "elsevier_scopus_inst_token": SecretStr("scopus-test-token"),
            },
            [ExternalAPI.OPENALEX, ExternalAPI.CROSSREF, ExternalAPI.UNPAYWALL],
            id="scopus-off-by-default-despite-credentials",
        ),
        pytest.param(
            {"use_scopus": True},
            [ExternalAPI.OPENALEX, ExternalAPI.CROSSREF, ExternalAPI.UNPAYWALL],
            id="use-scopus-still-needs-key",
        ),
    ],
)
def test_enabled_providers(
    test_settings: PipelineSettings,
    update: dict[str, Any],
    expected: list[ExternalAPI],
):
    fetcher = build_fetcher(test_settings.model_copy(update=update))

    assert enabled_providers(fetcher) == expected


def test_build_fetcher_passes_credentials(test_settings: PipelineSettings):
    """Storage settings are unset; building the fetcher must still work."""
    fetcher = build_fetcher(test_settings)
    openalex = fetcher.all_api_configs["fulltext"][ExternalAPI.OPENALEX.name]

    assert (
        openalex.headers["api_key"] == "openalex-test-key"  # pragma: allowlist secret
    )
    assert openalex.query_params is not None
    assert openalex.query_params["mailto"] == "pipeline@example.org"
