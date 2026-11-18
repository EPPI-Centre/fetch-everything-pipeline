"""Define tests for the steering code."""

from pathlib import Path

import pytest
from fer.blob_storage import BlobUploadError
from fer.fetch_fulltext import FullTextBatchFetcher
from pytest_mock import MockerFixture, MockType

import fetch_everything_pipeline
from fetch_everything_pipeline import (
    PDF_DIR_NAME,
    RESULTS_FILE_NAME,
    InputFileError,
    log_summary,
    main,
    parse_args,
    read_dois,
)
from fetch_everything_pipeline.fetch import DEFAULT_CHUNK_SIZE
from fetch_everything_pipeline.models import PipelineResult, Status, read_results
from fetch_everything_pipeline.settings import PipelineSettings
from tests.fakes import is_unique_blob_name, mock_fetch


@pytest.fixture
def input_file(tmp_path: Path) -> Path:
    path = tmp_path / "dois.txt"
    test_doi_list = ["10.1000/a", "not a doi", "https://doi.org/10.1000/A", "10.1000/b"]
    path.write_text("\n".join(test_doi_list) + "\n")

    return path


def test_read_dois_happy_path(tmp_path: Path):
    path = tmp_path / "dois.txt"
    test_doi_list = ["10.1000/a", "10.1000/b"]
    path.write_text("\n".join(test_doi_list) + "\n")

    assert (
        read_dois(path) == test_doi_list
    ), "should read DOIs from a file with one DOI per line"


def test_read_dois_strips_and_skips_blank_lines(tmp_path: Path):
    path = tmp_path / "dois.txt"
    test_doi_list = ["  10.1000/a  ", "", "   ", "10.1000/b\r\n"]
    path.write_text("\n".join(test_doi_list) + "\n")
    expected_dois = [item.strip() for item in test_doi_list if item.strip()]
    assert (
        read_dois(path) == expected_dois
    ), "should strip whitespace and skip blank lines"


def test_read_dois_ignores_byte_order_mark(tmp_path: Path):
    path = tmp_path / "dois.txt"
    test_doi_list = ["﻿10.1000/a", "10.1000/b"]
    path.write_text("\n".join(test_doi_list), encoding="utf-8")

    assert read_dois(path) == [
        "10.1000/a",
        "10.1000/b",
    ], "should ignore a leading byte order mark"


def test_read_dois_keeps_duplicates_and_invalid_lines(tmp_path: Path):
    path = tmp_path / "dois.txt"
    test_doi_list = ["10.1000/a", "not a doi", "10.1000/a"]
    path.write_text("\n".join(test_doi_list) + "\n")

    assert read_dois(path) == test_doi_list, "should keep duplicates and invalid lines"


@pytest.mark.parametrize("content", ["", "\n  \n"])
def test_read_dois_rejects_file_without_dois(tmp_path: Path, content: str):
    path = tmp_path / "dois.txt"
    path.write_text(content)

    with pytest.raises(InputFileError, match="No DOIs found in input file"):
        read_dois(path)


def test_read_dois_rejects_missing_file(tmp_path: Path):
    with pytest.raises(InputFileError, match="Cannot read input file"):
        read_dois(tmp_path / "missing.txt")


def test_read_dois_rejects_non_utf8_file(tmp_path: Path):
    path = tmp_path / "dois.txt"
    path.write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(InputFileError, match="Cannot read input file"):
        read_dois(path)


def test_parse_args_defaults_chunk_size():
    args = parse_args(["--input", "dois.txt", "--output", "out"])

    assert args.input == Path("dois.txt"), "should parse input file path"
    assert args.output == Path("out"), "should parse output file path"
    assert (
        args.chunk_size == DEFAULT_CHUNK_SIZE
    ), "should default chunk size to DEFAULT_CHUNK_SIZE"
    assert not args.upload, "should default upload flag to False if not specified"


def test_parse_args_upload_flag():
    args = parse_args(["--input", "dois.txt", "--output", "out", "--upload"])

    assert args.upload, "should set upload flag to True when specified"


@pytest.mark.parametrize(
    "input_args",
    [
        ("--input", "dois.txt", "--output", "out", "--chunk-size", "5"),
        ("--input", "dois.txt", "--output", "out"),
        ("--input", "dois.txt", "--output", "out", "--upload"),
    ],
)
def test_parse_args_reads_all_arguments(input_args: tuple[str, ...]):
    args = parse_args(input_args)

    expected_chunk_size = (
        int(input_args[-1]) if "--chunk-size" in input_args else DEFAULT_CHUNK_SIZE
    )
    expected_upload_flag = "--upload" in input_args
    assert args.chunk_size == expected_chunk_size, "should parse chunk size"
    assert args.upload == expected_upload_flag, "should parse upload flag"


@pytest.mark.parametrize(
    "argv",
    [
        ["--output", "out"],
        ["--input", "dois.txt"],
        ["--input", "a", "--output", "b", "--chunk-size", "0"],
        ["--input", "a", "--output", "b", "--chunk-size", "many"],
        ["--input", "dois.txt", "--output", "out", "--upload=Twenty"],
    ],
)
def test_parse_args_rejects_bad_arguments(argv: list[str]):
    with pytest.raises(SystemExit) as exit_info:
        parse_args(argv)

    expected_argparse_exit_code = 2
    assert (
        exit_info.value.code == expected_argparse_exit_code
    ), "should exit with the expected code for invalid arguments"


@pytest.mark.usefixtures("cli_fetcher_with_pdf")
def test_main_writes_results_for_every_input(
    input_file: Path,
    tmp_path: Path,
):
    output = tmp_path / "out"

    expected_result_status = [
        Status.FETCHED,
        Status.INVALID_DOI,
        Status.FETCHED,
        Status.NOT_FOUND,
    ]
    exit_code = main(["--input", str(input_file), "--output", str(output)])
    input_dois = read_dois(input_file)

    assert exit_code == 0, "main() should return 0 on success"
    results = read_results(output / RESULTS_FILE_NAME)
    assert [
        result.input_doi for result in results
    ] == input_dois, "results should be in the same order as the input DOIs"
    assert [
        result.status for result in results
    ] == expected_result_status, "results should have the expected statuses"

    assert all(
        result.path is not None for result in results if result.status == Status.FETCHED
    ), "results for fetched DOIs should have a path"
    assert all(
        result.path.parent == output / PDF_DIR_NAME
        for result in results
        if result.status == Status.FETCHED and result.path is not None
    ), "results for fetched DOIs should be in the expected PDF directory"
    assert (output / PDF_DIR_NAME).is_dir(), "the PDF directory should be created"


@pytest.mark.usefixtures("cli_fetcher_with_pdf", "mock_blob_client")
def test_main_uploads_before_writing_results(
    mocker: MockerFixture,
    uploaded_files: list[tuple[bytes, str]],
    input_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("STORAGE_BLOB_ACCOUNT", "testaccount")
    monkeypatch.setenv("STORAGE_BLOB_CONTAINER", "testcontainer")
    output = tmp_path / "out"
    calls = mocker.Mock()
    calls.attach_mock(
        mocker.spy(fetch_everything_pipeline, "upload_results"), "upload_results"
    )
    calls.attach_mock(
        mocker.spy(fetch_everything_pipeline, "write_results"), "write_results"
    )

    exit_code = main(["--input", str(input_file), "--output", str(output), "--upload"])

    assert exit_code == 0, "Should return 0 on success"
    assert [name for name, _, _ in calls.mock_calls] == [
        "upload_results",
        "write_results",
    ], "Expect the exact call order to be upload_results followed by write_results"
    (pdf_bytes, pdf_blob), (manifest_bytes, manifest_blob) = uploaded_files
    results_path = output / RESULTS_FILE_NAME
    first_result = read_results(results_path)[0]
    assert first_result.path is not None, "First result should have a path"
    assert (
        pdf_bytes == first_result.path.read_bytes()
    ), "PDF bytes should match the first result's path content"
    assert (
        first_result.blob_name == pdf_blob
    ), "The result's blob name should match the uploaded PDF blob"
    assert is_unique_blob_name(
        pdf_blob, first_result.path
    ), "The PDF blob name should be the PDF stem plus a unique suffix"
    assert (
        manifest_bytes == results_path.read_bytes()
    ), "Manifest bytes should match the content of the results file"
    assert manifest_blob.endswith(
        f"/{RESULTS_FILE_NAME}"
    ), "Manifest blob should end with the results file name"


<<<<<<< HEAD
@pytest.mark.usefixtures("cli_fetcher_with_pdf")
def test_main_writes_results_when_blob_client_cannot_be_created(
    mock_blob_client: MockType,
    input_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("STORAGE_BLOB_ACCOUNT", "testaccount")
    monkeypatch.setenv("STORAGE_BLOB_CONTAINER", "testcontainer")
    mock_blob_client.side_effect = BlobUploadError("no credential")
    output = tmp_path / "out"

    exit_code = main(["--input", str(input_file), "--output", str(output), "--upload"])

    assert exit_code == 1, "The manifest upload also fails, so main should return 1"
    results = read_results(output / RESULTS_FILE_NAME)
    assert [result.status for result in results] == [
        Status.UPLOAD_FAILED,
        Status.INVALID_DOI,
        Status.UPLOAD_FAILED,
        Status.NOT_FOUND,
    ], "Fetched results should be saved locally as upload_failed"


=======
>>>>>>> 70db64f (Adds blob upload capability)
@pytest.mark.usefixtures("test_settings")
def test_main_rejects_missing_blob_settings_before_fetching(
    mocker: MockerFixture,
    test_fetcher: FullTextBatchFetcher,
    input_file: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    build_fetcher_mock = mocker.patch(
        "fetch_everything_pipeline.build_fetcher", return_value=test_fetcher
    )
    fetch_mock = mocker.patch("fetch_everything_pipeline.fetch_fulltexts")

    exit_code = main(
        ["--input", str(input_file), "--output", str(tmp_path / "out"), "--upload"]
    )

    assert exit_code == 1, "Should return 1 when blob settings are missing"
    assert (
        "'STORAGE_BLOB_ACCOUNT', 'STORAGE_BLOB_CONTAINER'" in caplog.text
    ), "Should log missing blob settings"
    (
        build_fetcher_mock.assert_not_called(),
        "build_fetcher should not be called when blob settings are missing",
    )
    (
        fetch_mock.assert_not_awaited(),
        "fetch_fulltexts should not be awaited when blob settings are missing",
    )


def test_main_passes_settings_to_build_fetcher(
    test_settings: PipelineSettings,
    mocker: MockerFixture,
    test_fetcher: FullTextBatchFetcher,
    input_file: Path,
    tmp_path: Path,
):
    build_fetcher_mock = mocker.patch(
        "fetch_everything_pipeline.build_fetcher", return_value=test_fetcher
    )

    main(["--input", str(input_file), "--output", str(tmp_path / "out")])

    [settings], _ = build_fetcher_mock.call_args
    assert isinstance(
        settings, PipelineSettings
    ), "build_fetcher() should be called with a PipelineSettings instance"
    assert settings.mailto == test_settings.mailto, "mailto should be as expected"


def test_main_uses_default_chunk_size(
    results: list[PipelineResult],
    mocker: MockerFixture,
    test_fetcher: FullTextBatchFetcher,
    input_file: Path,
    tmp_path: Path,
):
    mocker.patch("fetch_everything_pipeline.build_fetcher", return_value=test_fetcher)
    fetch_fulltexts_mock = mocker.patch(
        "fetch_everything_pipeline.fetch_fulltexts", return_value=results
    )
    output = tmp_path / "out"

    main(["--input", str(input_file), "--output", str(output)])

    (
        fetch_fulltexts_mock.assert_awaited_once_with(
            read_dois(input_file),
            output / PDF_DIR_NAME,
            test_fetcher,
            DEFAULT_CHUNK_SIZE,
        ),
        "fetch_fulltexts() should be called with the default chunk size when --chunk-size is not provided",
    )


@pytest.mark.parametrize(
    "chunk_size",
    [
        1,
        5,
        10,
    ],
)
def test_main_passes_chunk_size_option(
    results: list[PipelineResult],
    mocker: MockerFixture,
    test_fetcher: FullTextBatchFetcher,
    input_file: Path,
    tmp_path: Path,
    chunk_size: int,
):
    mocker.patch("fetch_everything_pipeline.build_fetcher", return_value=test_fetcher)
    fetch_fulltexts_mock = mocker.patch(
        "fetch_everything_pipeline.fetch_fulltexts", return_value=results
    )
    output = tmp_path / "out"
    main(
        [
            "--input",
            str(input_file),
            "--output",
            str(output),
            "--chunk-size",
            f"{chunk_size}",
        ]
    )

    (
        fetch_fulltexts_mock.assert_awaited_once_with(
            read_dois(input_file), output / PDF_DIR_NAME, test_fetcher, chunk_size
        ),
        "fetch_fulltexts() should be called with the specified chunk size",
    )


def test_main_fails_on_missing_input_file(
    mocker: MockerFixture,
    test_fetcher: FullTextBatchFetcher,
    test_settings: PipelineSettings,
    tmp_path: Path,
):
    output = tmp_path / "out"
    build_fetcher_mock = mocker.patch(
        "fetch_everything_pipeline.build_fetcher", return_value=test_fetcher
    )
    exit_code = main(
        ["--input", str(tmp_path / "missing.txt"), "--output", str(output)]
    )

    assert exit_code == 1, "main() should return 1 when the input file is missing"
    assert (
        not output.exists()
    ), "main() should not create the output directory when the input file is missing"
    (
        build_fetcher_mock.assert_not_called(),
        "build_fetcher() should not be called when the input file is missing",
    )


def test_main_fails_on_invalid_settings(
    test_settings: PipelineSettings,
    test_fetcher: FullTextBatchFetcher,
    mocker: MockerFixture,
    input_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    build_fetcher_mock = mocker.patch(
        "fetch_everything_pipeline.build_fetcher", return_value=test_fetcher
    )
    monkeypatch.setenv("MAILTO", "not an email")
    output = tmp_path / "out"

    exit_code = main(["--input", str(input_file), "--output", str(output)])

    assert exit_code == 1, "main() should return 1 when the settings are invalid"
    assert (
        not output.exists()
    ), "main() should not create the output directory when the settings are invalid"
    (
        build_fetcher_mock.assert_not_called(),
        "build_fetcher() should not be called when the settings are invalid",
    )


def test_main_fails_on_non_empty_output_dir(
    test_settings: PipelineSettings,
    mocker: MockerFixture,
    test_fetcher: FullTextBatchFetcher,
    input_file: Path,
    tmp_path: Path,
):
    mocker.patch("fetch_everything_pipeline.build_fetcher", return_value=test_fetcher)
    output = tmp_path / "out"
    output.mkdir()
    (output / RESULTS_FILE_NAME).write_text("stale\n")

    exit_code = main(["--input", str(input_file), "--output", str(output)])

    assert (
        exit_code == 1
    ), "main() should return 1 when the output directory is not empty"
    assert (
        (output / RESULTS_FILE_NAME).read_text() == "stale\n"
    ), "main() should not overwrite files in a non-empty output directory"
    (
        test_fetcher.get_many_fulltext_pdfs_cycling_apis.assert_not_called(),
        "the fetcher should not be called when the output directory is not empty",
    )


def test_main_succeeds_when_every_fetch_fails(
    test_settings: PipelineSettings,
    mocker: MockerFixture,
    test_fetcher: FullTextBatchFetcher,
    input_file: Path,
    tmp_path: Path,
):
    mocker.patch("fetch_everything_pipeline.build_fetcher", return_value=test_fetcher)
    test_fetcher.get_many_fulltext_pdfs_cycling_apis.side_effect = mock_fetch(
        {}, fail_on=frozenset({"10.1000/a"})
    )
    output = tmp_path / "out"

    exit_code = main(["--input", str(input_file), "--output", str(output)])

    assert (
        exit_code == 0
    ), "main should return a success code even when every fetch fails"
    statuses = {r.status for r in read_results(output / RESULTS_FILE_NAME)}
    assert (
        statuses == {Status.ERROR, Status.INVALID_DOI}
    ), "results should contain only ERROR and INVALID_DOI statuses when every fetch fails"


def test_log_summary_counts_every_status(
    caplog: pytest.LogCaptureFixture, results: list[PipelineResult]
):
    log_summary(results)
    number_of_fetched = sum(1 for r in results if r.status == Status.FETCHED)
    number_of_not_found = sum(1 for r in results if r.status == Status.NOT_FOUND)
    number_of_invalid_doi = sum(1 for r in results if r.status == Status.INVALID_DOI)
    number_of_error = sum(1 for r in results if r.status == Status.ERROR)
    number_of_upload_failed = sum(
        1 for r in results if r.status == Status.UPLOAD_FAILED
    )

    assert caplog.messages == [
        f"Processed {len(results)} inputs",
        f"fetched: {number_of_fetched}",
        f"not_found: {number_of_not_found}",
        f"invalid_doi: {number_of_invalid_doi}",
        f"error: {number_of_error}",
        f"upload_failed: {number_of_upload_failed}",
    ]
