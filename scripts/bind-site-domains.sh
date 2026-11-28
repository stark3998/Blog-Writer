#!/usr/bin/env bash
# Bind the portfolio's public host names (infra/v2 site_hosts) to the v2 container app, with free
# Azure-managed certificates. Safe to re-run: each step checks what is already in place.
#
# DNS (at the DNS provider, "DNS only" / not proxied), in this order:
#   1. TXT  asuid.<apex>          = <environment custom domain verification ID>   (no traffic impact)
#      TXT  asuid.www.<apex>      = same value                                      (no traffic impact)
#      -> run this script: it adds the host names to the app (no certificate yet). The site can
#         then be tested before any traffic moves:
#         curl -k --resolve <apex>:443:<environment IP> https://<apex>/
#   2. A    <apex>                = <environment static IP>   (replaces the GitHub Pages A records)
#      CNAME www.<apex>           = <app FQDN>
#      -> run this script again: it requests the managed certificates (HTTP validation for the
#         apex, CNAME validation for www) and binds them. Issuance takes a few minutes; until
#         then HTTPS on the moved names shows a certificate warning, so do step 2 at a quiet time.
#
# Usage: scripts/bind-site-domains.sh [apex]   (default: the first entry of site_hosts)
# Needs: az login (Contributor on the resource group), dig, terraform outputs of infra/v2.
set -euo pipefail

root=$(git rev-parse --show-toplevel)
platform="$root/infra/v2"
group=rg-blog-writer-dev
app=$(terraform -chdir="$platform" output -raw container_app_name 2>/dev/null || echo ca-blog-writer-v2)
env_name=$(az containerapp show -g "$group" -n "$app" --query "properties.environmentId" -o tsv | awk -F/ '{print $NF}')
apex=${1:-$(terraform -chdir="$platform" output -json site_hosts 2>/dev/null | jq -r '.[0] // empty')}
[[ -n "$apex" ]] || { echo "no apex given and site_hosts is empty" >&2; exit 1; }
www="www.$apex"

read -r env_ip verify_id app_fqdn < <(
  az containerapp env show -g "$group" -n "$env_name" \
    --query "[properties.staticIp, properties.customDomainConfiguration.customDomainVerificationId]" -o tsv | tr '\n' ' '
  az containerapp show -g "$group" -n "$app" --query "properties.configuration.ingress.fqdn" -o tsv
)

txt() { dig +short TXT "$1" | tr -d '"'; }
bound() { az containerapp hostname list -g "$group" -n "$app" --query "[?name=='$1'].bindingType" -o tsv; }

echo "app $app ($app_fqdn), environment $env_name ($env_ip)"
echo "DNS records this needs:"
printf '  TXT   asuid.%-22s %s\n' "$apex" "$verify_id"
printf '  TXT   asuid.%-22s %s\n' "$www" "$verify_id"
printf '  A     %-28s %s\n' "$apex" "$env_ip"
printf '  CNAME %-28s %s\n' "$www" "$app_fqdn"
echo

for host in "$apex" "$www"; do
  if [[ "$(txt "asuid.$host")" != "$verify_id" ]]; then
    echo "$host: TXT asuid.$host is not set yet; add it and re-run."
    continue
  fi
  if [[ -z "$(bound "$host")" ]]; then
    az containerapp hostname add -g "$group" -n "$app" --hostname "$host" >/dev/null
    echo "$host: added to the app (no certificate yet)."
  fi
  if [[ "$(bound "$host")" == "SniEnabled" ]]; then
    echo "$host: bound with a certificate."
    continue
  fi
  if [[ "$host" == "$apex" ]]; then
    points=$(dig +short A "$host" | sort | tr '\n' ' ')
    [[ "$points" == "$env_ip " ]] || { echo "$host: A record is '$points'; point it at $env_ip only, then re-run."; continue; }
    method=HTTP
  else
    points=$(dig +short CNAME "$host" | sed 's/\.$//')
    [[ "$points" == "$app_fqdn" ]] || { echo "$host: CNAME is '$points'; point it at $app_fqdn, then re-run."; continue; }
    method=CNAME
  fi
  echo "$host: requesting a managed certificate ($method validation; this can take several minutes)..."
  az containerapp hostname bind -g "$group" -n "$app" --hostname "$host" \
    --environment "$env_name" --validation-method "$method" >/dev/null
  echo "$host: bound."
done

echo
az containerapp hostname list -g "$group" -n "$app" -o table
