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
- `--output`: a new or empty directory. PDFs are written to `out/pdfs/` and one result per input line to `out/results.csv`.
- `--chunk-size` (optional, default 50): number of unique DOIs passed to the fetcher at once.
- `--upload` (optional, for dev/testing use locally): uploads each fetched PDF to the configured container, using the PDF filename as the blob name. It also uploads `results.csv` to `results/<UTC timestamp>-<run-id>/results.csv` so each run's DOI-to-blob mapping is retained. PDF upload failures are recorded as `upload_failed`; other DOIs continue.

Per-DOI failures are recorded in the results; the command exits with 1 only when the input file, settings or output directory can't be used.

### Results file

`results.csv` has a header row, then one row per input line, in input order (duplicates and invalid lines included). Empty cells mean "no value".

| Column           | Contents                                                                                                   |
| ---------------- | ---------------------------------------------------------------------------------------------------------- |
| `index`          | Position of the DOI in the input, from 0.                                                                  |
| `input_doi`      | The input line exactly as given (after trimming whitespace). Join on this to match results to your inputs. |
| `normalised_doi` | The DOI after removing prefixes and lower-casing; empty if invalid.                                        |
| `status`         | `fetched`, `not_found`, `invalid_doi`, `error` or `upload_failed`.                                         |
| `source`         | The provider that supplied the PDF, when fetched.                                                          |
| `path`           | The PDF's location **relative to the folder containing `results.csv`**, e.g. `pdfs/<name>.pdf`.            |
| `blob_name`      | The uploaded blob's name, with `--upload` only.                                                            |
| `error`          | What went wrong, for `error` and `upload_failed`.                                                          |

Inputs with the same DOI share one PDF, so several rows can have the same `path`. To find a PDF, join the location you chose for the output (a local folder, or the Azure ML output URI) with `path`.

### Container smoke test

The Dockerfile uses BuildKit cache mounts, so build it with a BuildKit-enabled Docker installation. The image has no entrypoint, so give the command explicitly (running it with no command prints the help).

```bash
docker build -t fetch-everything-pipeline .
docker run --rm fetch-everything-pipeline fetch-pipeline --help
```

To check a real run, pass the settings and mount an input file and an output directory:

```bash
docker run --rm --env-file .env \
  -v "$PWD/dois.txt:/data/dois.txt:ro" -v "$PWD/out:/data/out" \
  fetch-everything-pipeline fetch-pipeline --input /data/dois.txt --output /data/out
```

The container runs as a non-root user, so the mounted output directory must be writable by it.

### Configuration

Settings are read from environment variables or a `.env` file in the working directory:

| Variable                     | Required                                     | Purpose                                            |
| ---------------------------- | -------------------------------------------- | -------------------------------------------------- |
| `MAILTO`                     | No (falls back to a default but recommended) | Contact email sent to OpenAlex and Unpaywall.      |
| `OPENALEX_KEY`               | No (but recommended)                         | OpenAlex API key. Without it, OpenAlex is skipped. |
| `ELSEVIER_SCOPUS_KEY`        | No                                           | Scopus API key. Without it, Scopus is skipped.     |
| `ELSEVIER_SCOPUS_INST_TOKEN` | Off-campus Scopus                            | Elsevier institution token.                        |
| `SCOPUS_PROXY_URL`           | No                                           | Proxy for Scopus requests.                         |
| `STORAGE_BLOB_ACCOUNT`       | With `--upload`                              | Azure Storage account name.                        |
| `STORAGE_BLOB_CONTAINER`     | With `--upload`                              | Container to receive the PDFs.                     |

Uploads use `DefaultAzureCredential` (no storage account key is needed): sign in with `az login` locally or assign the Azure ML compute a managed identity with the **Storage Blob Data Contributor** role. The manifest's run ID comes from `FETCH_PIPELINE_RUN_ID` if set, otherwise `AZUREML_RUN_ID` if available, and a random ID is used if neither is set.
