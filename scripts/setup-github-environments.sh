#!/usr/bin/env bash
# Configure the GitHub environments the v2 pipelines need, from the live Terraform outputs.
#
#   deploy  (deploy.yml): runs after a successful CI run on main, OIDC as the ci_deploy identity.
#           Variables ACR_NAME, RESOURCE_GROUP, CONTAINER_APP, CONTAINER_NAME; secrets AZURE_CLIENT_ID,
#           AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID. Deployment branch policy: main only.
#   infra   (infra.yml): plans on pull requests, applies on manual dispatch, OIDC as ci_infra.
#           Variables TF_STATE_RESOURCE_GROUP, TF_STATE_STORAGE_ACCOUNT, TF_STATE_CONTAINER;
#           secrets AZURE_* plus TF_VARS_FILE (infra/v2/data/terraform.tfvars) and
#           TF_VARS_FILE_PLATFORM (infra/v2/terraform.tfvars).
#
# Run it from the repository root after `az login` and `gh auth login`, whenever an identity, a
# tfvars file or the app name changes. It is idempotent and prints no secret values.
# infra/v2/entra is applied by the owner only and is not part of CI (it needs Graph rights).
#
# Usage: scripts/setup-github-environments.sh [owner/repo]   (default: this checkout's origin)
set -euo pipefail

repo=${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}
root=$(git rev-parse --show-toplevel)
platform="$root/infra/v2"

for file in "$platform/terraform.tfvars" "$platform/data/terraform.tfvars"; do
  [[ -s "$file" ]] || { echo "missing $file" >&2; exit 1; }
done

ids=$(terraform -chdir="$platform" output -json identity_client_ids)
deploy_client_id=$(jq -r .ci_deploy <<<"$ids")
infra_client_id=$(jq -r .ci_infra <<<"$ids")
tenant_id=$(az account show --query tenantId -o tsv)
subscription_id=$(az account show --query id -o tsv)
registry=$(terraform -chdir="$platform" output -raw acr_name 2>/dev/null || echo blogwriterdeveus6c2uw5)
app=$(terraform -chdir="$platform" output -raw container_app_name 2>/dev/null || echo ca-blog-writer-v2)
group=rg-blog-writer-dev

secret() { printf '%s' "$3" | gh secret set "$2" --env "$1" --repo "$repo" >/dev/null; echo "  secret $1/$2 set"; }
var() { gh variable set "$2" --env "$1" --repo "$repo" --body "$3" >/dev/null; echo "  variable $1/$2 = $3"; }

echo "deploy environment (main only)"
gh api -X PUT "repos/$repo/environments/deploy" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
if ! gh api "repos/$repo/environments/deploy/deployment-branch-policies" --jq '.branch_policies[].name' | grep -qx main; then
  gh api -X POST "repos/$repo/environments/deploy/deployment-branch-policies" -f name=main -f type=branch >/dev/null
fi
var deploy ACR_NAME "$registry"
var deploy RESOURCE_GROUP "$group"
var deploy CONTAINER_APP "$app"
var deploy CONTAINER_NAME app
secret deploy AZURE_CLIENT_ID "$deploy_client_id"
secret deploy AZURE_TENANT_ID "$tenant_id"
secret deploy AZURE_SUBSCRIPTION_ID "$subscription_id"

echo "infra environment (plans on PRs, applies on dispatch)"
gh api -X PUT "repos/$repo/environments/infra" >/dev/null
var infra TF_STATE_RESOURCE_GROUP "$group"
var infra TF_STATE_STORAGE_ACCOUNT blogwritertfstate
var infra TF_STATE_CONTAINER tfstate
secret infra AZURE_CLIENT_ID "$infra_client_id"
secret infra AZURE_TENANT_ID "$tenant_id"
secret infra AZURE_SUBSCRIPTION_ID "$subscription_id"
gh secret set TF_VARS_FILE --env infra --repo "$repo" <"$platform/data/terraform.tfvars" >/dev/null
echo "  secret infra/TF_VARS_FILE set (infra/v2/data/terraform.tfvars)"
gh secret set TF_VARS_FILE_PLATFORM --env infra --repo "$repo" <"$platform/terraform.tfvars" >/dev/null
echo "  secret infra/TF_VARS_FILE_PLATFORM set (infra/v2/terraform.tfvars)"

echo "done: push a commit that touches infra/v2/** on a pull request to see the plans run."
