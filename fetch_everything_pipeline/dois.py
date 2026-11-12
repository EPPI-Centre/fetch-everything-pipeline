"""Prepare input DOIs for fetching."""

from uuid import UUID, uuid5

from destiny_sdk.identifiers import DOIIdentifier
from fer.fetching.core import DOIStudy, DOIStudyCollection
from fer.local.run import DOI_NAMESPACE
from fer.utils import InvalidDOIError, validate_doi
from pydantic import BaseModel, Field

from fetch_everything_pipeline.models import PipelineResult, Status


class PreparedDOIs(BaseModel):
    """
    Input DOIs, validated and deduplicated, ready to pass to FER's fetcher.

    Every input index should only appear once: either in `invalid`, or in one of
    the lists in `input_indices`.

    Attributes:
        studies: One study per unique normalised DOI, in order of first
            appearance in the input.
        input_indices: For each study `uid`, the indices of every input that
            normalised to that DOI, in ascending order.
        invalid: An `invalid_doi` result for each input that failed validation.

    """

    studies: DOIStudyCollection = Field(default_factory=DOIStudyCollection)
    input_indices: dict[UUID, list[int]] = Field(default_factory=dict)
    invalid: list[PipelineResult] = Field(default_factory=list)


def prepare_dois(dois: list[str]) -> PreparedDOIs:
    """
    Validate, normalise and deduplicate input DOIs.

    Each input has surrounding whitespace removed and is then normalised with
    FER's `validate_doi`, which strips prefixes
    such as `https://doi.org/` and `doi:` and lower-cases the DOI.

    Studies are built as in `fer.local.run.generate_study_collection_from_dois`,
    so each DOI gets the same `uid` (and therefore the same `{uid}.pdf` file name) as
    it would in the robot.

    Inputs that fail validation are recorded as `invalid_doi` results.

    Args:
        dois (list[str]): The input DOIs in the order they were supplied.

    Returns:
        PreparedDOIs: The unique studies to fetch, the mapping from each study
            back to its inputs, and the results for invalid inputs.

    """
    studies: dict[UUID, DOIStudy] = {}
    input_indices: dict[UUID, list[int]] = {}
    invalid: list[PipelineResult] = []

    for index, input_doi in enumerate(dois):
        try:
            doi = DOIIdentifier(identifier=validate_doi(input_doi.strip()))
        except InvalidDOIError as invalid_doi_error:
            invalid.append(
                PipelineResult(
                    index=index,
                    input_doi=input_doi,
                    status=Status.INVALID_DOI,
                    error=str(invalid_doi_error),
                )
            )
            continue

        uid = uuid5(DOI_NAMESPACE, doi.identifier)
        if uid not in studies:
            studies[uid] = DOIStudy(doi=doi, uid=uid)
        input_indices.setdefault(uid, []).append(index)

    return PreparedDOIs(
        studies=DOIStudyCollection(studies=list(studies.values())),
        input_indices=input_indices,
        invalid=invalid,
    )
