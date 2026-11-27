#!/usr/bin/env bash
set -euo pipefail

: "${KEY_VAULT_URL:?Set KEY_VAULT_URL to the deployment's Key Vault URL}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

az ml component create --file "${script_dir}/component.yml" "$@"
az ml component create --file "${script_dir}/pipeline-component.yml" \
  --set "inputs.key_vault_url.default=${KEY_VAULT_URL}" "$@"
