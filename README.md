# fetch-everything-pipeline

A deployable pipeline to target Azure ML, based on fetching logic from the fetch-everything-robot

## Usage

```bash
uv sync
uv run fetch-pipeline --input dois.txt --output out/
```

Add `--upload` to send fetched PDFs to Azure Blob Storage:

```bash
uv run fetch-pipeline --input dois.txt --output out/ --upload
```

- `--input`: a text file with one DOI per line. URL and `doi:` prefixes are accepted; blank lines are skipped.
- `--output`: a new or empty directory. PDFs are written to `out/pdfs/` and one result per input line to `out/results.jsonl`.
- `--chunk-size` (optional, default 50): number of unique DOIs passed to the fetcher at once.
- `--upload` (optional): uploads each fetched PDF to the configured container, using the PDF filename as the blob name. It also uploads `results.jsonl` to `results/<UTC timestamp>-<run-id>/results.jsonl` so each run's DOI-to-blob mapping is retained. PDF upload failures are recorded as `upload_failed`; other DOIs continue.

Each result has a `status` of `fetched`, `not_found`, `invalid_doi`, `error` or `upload_failed`, and `fetched` results include the `source` API. Per-DOI failures are recorded in the results; the command exits with 1 only when the input file, settings or output directory can't be used.

### Configuration

Settings are read from environment variables or a `.env` file in the working directory:

| Variable                     | Required          | Purpose                                            |
| ---------------------------- | ----------------- | -------------------------------------------------- |
| `MAILTO`                     | Yes               | Contact email sent to OpenAlex and Unpaywall.      |
| `OPENALEX_KEY`               | Yes               | OpenAlex API key. Without it, OpenAlex is skipped. |
| `ELSEVIER_SCOPUS_KEY`        | No                | Scopus API key. Without it, Scopus is skipped.     |
| `ELSEVIER_SCOPUS_INST_TOKEN` | Off-campus Scopus | Elsevier institution token.                        |
| `SCOPUS_PROXY_URL`           | No                | Proxy for Scopus requests.                         |
| `STORAGE_BLOB_ACCOUNT`       | With `--upload`   | Azure Storage account name.                        |
| `STORAGE_BLOB_CONTAINER`     | With `--upload`   | Container to receive the PDFs.                     |

Uploads use `DefaultAzureCredential` (no storage account key is needed): sign in with `az login` locally or assign the Azure ML compute a managed identity with the **Storage Blob Data Contributor** role. `PipelineSettings` reads `FETCH_PIPELINE_RUN_ID` first, then `AZUREML_RUN_ID` if available; if neither is set, a random ID is used. Set `FETCH_PIPELINE_RUN_ID` to the Azure ML job name in the job environment to make manifests easy to identify. The providers in use are logged at the start of each run.
