#!/usr/bin/env bash
# Configure the portfolio repository's GitHub "deploy" environment so its deploy workflow can build
# the portfolio image and roll the portfolio container of the shared Blog Writer v2 app (infra/v2
# site_hosts). GitHub OIDC as the portfolio-deploy identity; no secrets are stored anywhere.
#
#   deploy environment (default branch only)
#     variables  ACR_NAME, RESOURCE_GROUP, CONTAINER_APP
#     secrets    AZURE_CLIENT_ID (portfolio-deploy identity), AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID
#
# The federated credential trusts repo:<owner>/portfolio:environment:deploy, so the deploy job
# must declare `environment: deploy`. The portfolio container reads its data at runtime with its
# own identity, so the old GitHub Pages variables (COSMOS_*, BLOB_ENDPOINT) are not needed: delete
# them, and the COSMOS_KEY secret, once the site runs on Azure.
# Run from the Blog-Writer repository root after `az login` and `gh auth login`. Idempotent;
# prints no secret values.
#
# Usage: scripts/setup-portfolio-variables.sh [owner/portfolio-repo]   (default: <your login>/portfolio)
set -euo pipefail

root=$(git rev-parse --show-toplevel)
platform="$root/infra/v2"
repo=${1:-"$(gh api user --jq .login)/portfolio"}
branch=$(gh repo view "$repo" --json defaultBranchRef --jq .defaultBranchRef.name)

client_id=$(terraform -chdir="$platform" output -json identity_client_ids | jq -r .portfolio_deploy)
[[ "$client_id" != "null" && -n "$client_id" ]] || { echo "no portfolio_deploy identity: apply infra/v2 first" >&2; exit 1; }
tenant_id=$(az account show --query tenantId -o tsv)
subscription_id=$(az account show --query id -o tsv)
registry=$(terraform -chdir="$platform" output -raw acr_name 2>/dev/null || echo blogwriterdeveus6c2uw5)
app=$(terraform -chdir="$platform" output -raw container_app_name 2>/dev/null || echo ca-blog-writer-v2)
group=rg-blog-writer-dev

secret() { printf '%s' "$2" | gh secret set "$1" --env deploy --repo "$repo" >/dev/null; echo "  secret deploy/$1 set"; }
var() { gh variable set "$1" --env deploy --repo "$repo" --body "$2" >/dev/null; echo "  variable deploy/$1 = $2"; }

echo "$repo: deploy environment ($branch only)"
gh api -X PUT "repos/$repo/environments/deploy" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
if ! gh api "repos/$repo/environments/deploy/deployment-branch-policies" --jq '.branch_policies[].name' | grep -qx "$branch"; then
  gh api -X POST "repos/$repo/environments/deploy/deployment-branch-policies" -f name="$branch" -f type=branch >/dev/null
fi
var ACR_NAME "$registry"
var RESOURCE_GROUP "$group"
var CONTAINER_APP "$app"
secret AZURE_CLIENT_ID "$client_id"
secret AZURE_TENANT_ID "$tenant_id"
secret AZURE_SUBSCRIPTION_ID "$subscription_id"
echo "Done. A push to $branch now deploys the portfolio container after CI passes."
