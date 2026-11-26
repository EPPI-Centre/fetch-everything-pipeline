# fetch-everything-pipeline

A deployable pipeline to target Azure ML, based on fetching logic from the fetch-everything-robot

## Usage

```bash
uv sync
uv run fetch-pipeline --input dois.txt --output out/
```

- `--input`: a text file with one DOI per line. URL and `doi:` prefixes are accepted; blank lines are skipped.
- `--output`: a new or empty directory. PDFs are written to `out/pdfs/` and one result per input line to `out/results.jsonl`.
- `--chunk-size` (optional, default 50): number of unique DOIs passed to the fetcher at once.

Each result has a `status` of `fetched`, `not_found`, `invalid_doi`, `error` or `upload_failed`, and `fetched` results include the `source` API. Per-DOI failures are recorded in the results; the command exits with 1 only when the input file, settings or output directory can't be used.

### Configuration

Settings are read from environment variables or a `.env` file in the working directory:

| Variable                     | Required             | Purpose                                            |
| ---------------------------- | -------------------- | -------------------------------------------------- |
| `MAILTO`                     | Yes                  | Contact email sent to OpenAlex and Unpaywall.      |
| `OPENALEX_KEY`               | No (but recommended) | OpenAlex API key. Without it, OpenAlex is skipped. |
| `ELSEVIER_SCOPUS_KEY`        | No                   | Scopus API key. Without it, Scopus is skipped.     |
| `ELSEVIER_SCOPUS_INST_TOKEN` | Off-campus Scopus    | Elsevier institution token.                        |
| `SCOPUS_PROXY_URL`           | No                   | Proxy for Scopus requests.                         |

The providers in use are logged at the start of each run.
