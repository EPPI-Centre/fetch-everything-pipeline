#!/usr/bin/env bash
set -euo pipefail

: "${KEY_VAULT_URL:?Set KEY_VAULT_URL to the Key Vault URL for this deployment}"
: "${MAILTO:?Set MAILTO to the contact email for this deployment}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

az ml component create --file "${script_dir}/component.yml" "$@"
az ml component create --file "${script_dir}/pipeline-component.yml" \
  --set "inputs.key_vault_url.default=${KEY_VAULT_URL}" \
  "inputs.mailto.default=${MAILTO}" "$@"
