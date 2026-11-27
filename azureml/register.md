# Registering the components

`register.sh` registers the command component `component.yml` and then the pipeline component `pipeline-component.yml`.

The Key Vault URL is kept out of the repo. The script reads it from `KEY_VAULT_URL` and sets it as the registered pipeline component's `key_vault_url` default, so callers that omit that input (ADF, `invoke-test.yml`) keep working. The YAML file itself has no default, so registering it directly without one gives a required input.

## Usage

1. Bump `version` in both `component.yml` and `pipeline-component.yml`. Registering a version that already exists fails.
2. Run the script. `az ml` needs a resource group and workspace: either pass `--resource-group` and `--workspace-name` (the script passes them to both `az ml component create` calls), or set them once with `az configure --defaults group=<rg> workspace=<ws>`.

   ```bash
   KEY_VAULT_URL=https://<vault>.vault.azure.net/ \
     azureml/register.sh --resource-group <rg> --workspace-name <ws>
   ```

3. Check that the default was set before creating a deployment from it:

   ```bash
   az ml component show --name <pipeline-component> --version <n> -g <rg> -w <ws> --query inputs
   ```

## Rolling out

1. Create the new deployment without making it the endpoint's default.

   ```bash
   az ml batch-deployment create -f azureml/deployment.yml -g <rg> -w <ws>
   ```

2. Invoke it directly, leaving out `key_vault_url` as ADF does:

   ```bash
   az ml batch-endpoint invoke -n <endpoint> --deployment-name <new-deployment> -f azureml/invoke-test.yml -g <rg> -w <ws>
   ```

3. Only then make it the endpoint's default deployment. Until then the previous deployment keeps serving ADF, and rolling back is a matter of switching the default back.

   ```bash
   az ml batch-endpoint update -n <endpoint> --set defaults.deployment_name=<new-deployment> -g <rg> -w <ws>
   ```

## How `register.sh` works

- The command component is registered first. The pipeline component refers to it with `@latest`, which is resolved when the pipeline component is registered, so this order picks up the new command component.
- `--set "inputs.key_vault_url.default=..."` adds the default to the registered version only; the YAML file isn't changed.
- Extra arguments (`"$@"`) are passed to both `az ml component create` calls.
- The script stops if `KEY_VAULT_URL` is unset or empty, and at the first failed command, so the pipeline component is never registered on top of a failed command component.
