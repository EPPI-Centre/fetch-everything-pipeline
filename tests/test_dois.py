"""Tests for DOI preparation."""

import pytest
from fer.local.run import generate_study_collection_from_dois

from fetch_everything_pipeline.dois import PreparedDOIs, prepare_dois
from fetch_everything_pipeline.models import Status


def all_indices(prepared: PreparedDOIs) -> list[int]:
    """Return a sorted list of all input indices in `prepared`."""
    valid = [i for indices in prepared.input_indices.values() for i in indices]
    invalid = [result.index for result in prepared.invalid]
    return sorted(valid + invalid)


@pytest.mark.parametrize(
    "input_doi",
    [
        "10.1000/abc",
        "10.1000/ABC",
        "https://doi.org/10.1000/abc",
        "http://doi.org/10.1000/abc",
        "https://dx.doi.org/10.1000/abc",
        "http://dx.doi.org/10.1000/abc",
        "doi:10.1000/abc",
        "DOI: 10.1000/abc",
        "  10.1000/abc  ",
        "\t10.1000/abc\n",
        " https://doi.org/10.1000/abc ",
    ],
)
def test_normalises_prefixes_and_case(input_doi: str):
    prepared = prepare_dois([input_doi])

    assert [study.doi.identifier for study in prepared.studies.studies] == [
        "10.1000/abc"
    ]
    assert prepared.invalid == []


def test_uid_matches_fer():
    """The same DOI must get the same uid, and so the same file name, as in FER."""
    dois = ["10.1000/abc", "10.1000/xyz"]
    fer_uids = [
        study.uid for study in generate_study_collection_from_dois(dois).studies
    ]

    assert [study.uid for study in prepare_dois(dois).studies.studies] == fer_uids


def test_duplicates_share_one_study():
    prepared = prepare_dois(
        ["10.1000/abc", "https://doi.org/10.1000/ABC", "10.1000/xyz", "doi:10.1000/abc"]
    )

    abc, xyz = prepared.studies.studies
    assert abc.doi.identifier == "10.1000/abc"
    assert xyz.doi.identifier == "10.1000/xyz"
    assert prepared.input_indices == {abc.uid: [0, 1, 3], xyz.uid: [2]}


def test_padded_duplicate_shares_study_with_unpadded():
    prepared = prepare_dois(["10.1000/abc", " 10.1000/abc "])

    [study] = prepared.studies.studies
    assert prepared.input_indices == {study.uid: [0, 1]}


@pytest.mark.parametrize("input_doi", ["not a doi", "", "   ", "https://doi.org/"])
def test_invalid_input_becomes_invalid_doi_result(input_doi: str):
    prepared = prepare_dois(["10.1000/abc", input_doi])

    assert len(prepared.studies.studies) == 1
    [result] = prepared.invalid
    assert result.index == 1
    assert result.input_doi == input_doi
    assert result.normalised_doi is None
    assert result.status is Status.INVALID_DOI
    assert result.error


def test_every_input_accounted_for_once():
    dois = ["10.1000/abc", "bad", "10.1000/ABC", "", "10.1000/xyz", "bad"]

    assert all_indices(prepare_dois(dois)) == list(range(len(dois)))


def test_empty_list():
    prepared = prepare_dois([])

    assert prepared.studies.studies == []
    assert prepared.input_indices == {}
    assert prepared.invalid == []
