> **Status (2026-11-26).** Ready to apply into the existing footprint. The **data root**
> (`infra/v2/data/`) is applied and owns the v2 Cosmos DB account. This **platform root** has
> not been applied yet. It adds the v2 app **side by side** with v1 in the existing environment;
> v1 (`ca-blog-writer-dev` and its custom domain) keeps running until a later cutover.

# Blog Writer v2 platform (`infra/v2`)

Terraform for the v2 platform: a new container app `ca-blog-writer-v2` behind Easy Auth,
private Blob storage, Key Vault, Microsoft Foundry (GPT-6.2, Claude Opus 5.5), Web IQ, the
CI identities and out-of-band alerts. Everything goes into the **existing** resource group
`rg-blog-writer-dev` (East US).

- **Read, never managed or changed** (data sources): the resource group, the Log Analytics
  workspace `law-blog-writer-dev`, the Container Apps environment `cae-blog-writer-dev` (legacy
  Consumption-only, shared with v1), the container registry (shared with another app; its
  admin user stays as it is) and the v2 Cosmos DB account, database and `published-blogs`
  container from `infra/v2/data`.
- **Created and managed here:** five user-assigned identities, the container app and its Easy
  Auth config, the Entra app registration, Key Vault, the storage account, the Foundry account
  with its deployments, Web IQ, GitHub OIDC credentials, role assignments, an ACR purge task for
  the v2 repository only, the action group, log alert rules, the budget and locks.
- `infra/terraform` (v1) must never be applied again. `infra/v2/data` is a separate root with
  its own state; nothing here changes it.

This repository is **public**. Real values (subscription, tenant and object IDs, email
addresses, names) only go in `terraform.tfvars`, `backend.hcl` (both gitignored) or GitHub
environment secrets.

## Layout

| File | Contents |
|---|---|
| `versions.tf` | Providers (azurerm `~> 4.81`, azapi `~> 2.14`, time `~> 0.14`) and the partial `azurerm` backend. No azuread: CI plans and applies this root without Graph rights |
| `main.tf` | Data sources for the existing resources; the five identities |
| `containerapp.tf` | The v2 container app: `BW_*` settings, Key Vault references, probes |
| `easyauth.tf` | `authConfigs/current` (azapi): owner-only, allowed origins for Easy Auth's CSRF check. The Entra app registration lives in `entra/` |
| `keyvault.tf`, `storage.tf` | Key Vault; storage account, containers and lifecycle rules |
| `foundry.tf`, `webiq.tf` | Foundry account, project and deployments (Claude included); Web IQ |
| `acr.tf` | Purge task for the v2 repository of the shared registry |
| `rbac.tf`, `github_oidc.tf` | Every role assignment; GitHub federated credentials |
| `monitoring.tf`, `locks.tf`, `checks.tf` | Action group, log alerts, budget; locks; invariant checks |
| `terraform.tfvars.example`, `backend.hcl.example` | Committed placeholders. Copy them to the gitignored real files. |

The former `bootstrap/` root is gone. It created a new Entra-only state account in its own
resource group. State now lives in the existing state account `blogwritertfstate` (container
`tfstate`, key `blog-writer-v2-platform.tfstate`), accessed with the account key like v1 and
`infra/v2/data`, so there is nothing left to bootstrap. Switching to Entra-only state later
means uncommenting `use_azuread_auth` (and granting Storage Blob Data Contributor on the state
account); turning shared keys off on that account would also cut off v1's state.

## Who provisions what

| Item | Terraform | Owner |
|---|---|---|
| Identities: `app`, `easyauth`, `portfolio_build`, `ci_deploy`, `ci_infra` | ✓ | |
| Container app `ca-blog-writer-v2` (Consumption, 1 vCPU / 2 GiB, min = max = 1) | ✓ | |
| Easy Auth config (`infra/v2`, CI-managed) | ✓ | |
| Entra app, service principal (assignment required), owner assignment, federated credential, CLI access (`infra/v2/entra`) | ✓ (owner-applied root) | applies `entra/` |
| Key Vault (RBAC, purge protection) | ✓ | |
| Key Vault **secret values** | | ✓ (runbook step 3) |
| Storage account, containers and lifecycle rules | ✓ | |
| Foundry account and project; GPT-6.2 Sol and Astra, GPT-6 Luna, text-embedding-3-large, **Claude Opus 5.5** | ✓ | Marketplace terms, only if the Claude create asks for them |
| Web IQ account | ✓ (`enable_webiq`) | **bind the app identity in the Web IQ portal** |
| GitHub OIDC federated credentials, role assignments | ✓ | |
| ACR purge task (v2 repository only) | ✓ | |
| Action group, log alerts, advisory budget | ✓ | |
| CanNotDelete locks on storage, Foundry and Key Vault | ✓ (`enable_locks`) | |
| Container image | first revision only | deploy |
| LinkedIn redirect URI, portfolio PAT | | ✓ |
| Custom domain and certificate (cutover) | later | ✓ |

## How the lab policies are satisfied

| Policy | How |
|---|---|
| Allowed locations (no West US 3 or Sweden Central) | `location` and `image_location` are validated against the allowed list (defaults `eastus` and `eastus2`). Web IQ and the action group are `global`, which is exempt. |
| `Purpose`, `Owner`, `Expiry Date` tags | Required variables `tag_purpose`, `tag_owner`, `tag_expiry_date`, applied to every taggable resource created here (after the app tags, so they always win). The `lab_expiry_tag` check warns 30 days before expiry. |
| AI Services local auth disabled (modify/DINE) | `local_auth_enabled = false` on the Foundry accounts, plus a check. |
| Storage HTTPS only | `https_traffic_only_enabled = true`, TLS 1.2. |
| Public blob access (audit) | `allow_nested_items_to_be_public = false`, private containers. |
| Only `BizTalk Contributor` is denied | Roles used: AcrPull, AcrPush, Container Registry Tasks Contributor, Container Apps Contributor, Contributor, Foundry User, Key Vault Secrets User and Officer, Storage Blob Data Contributor and Reader, optionally Role Based Access Control Administrator; Cosmos DB built-in data roles. |

Terraform registers no resource providers (`resource_provider_registrations = "none"`, azapi
`skip_provider_registration = true`). Every provider used, `Microsoft.WebIQ` included, was
`Registered` on 2026-11-26.

**No secrets in state.** The registry, the workspace and the Cosmos account are read with azapi
(a plain ARM GET). The azurerm data sources for them would also call `listCredentials`,
`getSharedKeys` and `listKeys` and store a live registry admin password, workspace keys and
Cosmos account keys in the state.

## Prerequisites

- Terraform >= 1.9 and the Azure CLI, signed in to the lab subscription:
  `az login`, then `export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)`.
- Applies run **locally as the owner**: Owner (or Contributor + User Access Administrator) on
  `rg-blog-writer-dev` for role assignments and locks. The Entra app registration is a separate
  root, `infra/v2/entra`, that the owner (tenant admin) applies; its `easyauth_client_id`
  output goes into this root's `terraform.tfvars`.
- The data root is applied (it is), and the shared resources above exist under their default
  names (override the `*_name` variables otherwise).

## The Entra root (`infra/v2/entra`, owner only)

The app registration, its service principal (assignment required), the owner's assignment,
the easyauth managed identity as federated credential and the owner's CLI access. It needs
Microsoft Graph rights, so only the owner applies it, from a workstation:

```bash
cd infra/v2/entra
cp backend.hcl.example backend.hcl            # key blog-writer-v2-entra.tfstate
cp terraform.tfvars.example terraform.tfvars   # owner_object_id (+ custom_domain_hostname at cutover)
terraform init -backend-config=backend.hcl && terraform apply
terraform output -raw easyauth_client_id       # -> easyauth_client_id in ../terraform.tfvars
```

It reads the environment's default domain and the easyauth identity (created by this root)
through data sources, so apply `infra/v2` first on a fresh setup, then `entra/`, then
`infra/v2` again with `easyauth_client_id`. Re-apply it when the redirect URLs change (the
custom domain moves at cutover).

## Apply runbook

### 1. Configure and init

```bash
cd infra/v2
cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars   # tags, owner_object_id, alert_email, github_owner
terraform init -backend-config=backend.hcl
```

The backend uses the account key of `blogwritertfstate`, fetched with your Azure CLI sign-in
(the `resource_group_name` in `backend.hcl` is what makes that lookup possible).

### 2. First apply: no secrets, placeholder image

`terraform.tfvars` starts with `key_vault_secrets_set = []` and
`http_health_probes_enabled = false`. The first revision runs the placeholder
`mcr.microsoft.com/dotnet/samples:aspnetapp` (port 8080), which has no `/api/health`, so the
readiness and startup probes must be TCP until the real image runs: a revision that never gets
ready can make the apply wait for the deployment deadline and then fail.

```bash
terraform plan -out=tfplan      # local only; never commit or upload plans
terraform apply tfplan && rm -f tfplan
```

This creates everything listed above, Easy Auth included. The app is reachable without auth only
for the seconds between the app and its auth config being created, and then serves only the
sample page.

- **Claude:** if `azurerm_cognitive_deployment.claude` fails on the Marketplace offer, open the
  Foundry portal (project `blogwriter-v2` on account `foundry_account_name`), accept the Claude
  Opus 5.5 terms once, and apply again. If a deployment of the same name was created in the
  portal, import it instead:
  `terraform import 'azurerm_cognitive_deployment.claude[0]' <deployment resource id>`.
- **Web IQ:** kind `WebIQ.Grounding`, SKU `G3` and an empty `properties` object are unverified
  (no published schema for `2026-06-01-preview`). If the create fails, set `enable_webiq = false`,
  finish the runbook and come back to it.
- **Propagation:** a brand-new identity can take a minute to replicate. If a role assignment
  or a Cosmos DB SQL role assignment fails with "principal not found", apply again.

### 3. Set the Key Vault secrets

You have **Key Vault Secrets Officer** on the vault. Values never pass through Terraform, CI or
state; the app reads them through versionless references. Use files and `--output none`
(`az keyvault secret set` otherwise prints the value).

```bash
KV=$(terraform output -raw key_vault_name)
umask 077; f=$(mktemp)

# cookie-key -> BW_COOKIE_KEY (required: the app refuses to start in prod without it)
openssl rand -base64 48 | tr -d '\n' > "$f"
az keyvault secret set --vault-name "$KV" --name cookie-key --file "$f" --output none

# vault-keys -> BW_VAULT_KEYS: kid:base64url(32 random bytes)
printf '%s:%s' "$(date -u +%Y-%m)" "$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')" > "$f"
az keyvault secret set --vault-name "$KV" --name vault-keys --file "$f" --output none

# vapid-private-key -> BW_NOTIFY_VAPID_PRIVATE_KEY (P-256, base64url DER)
openssl ecparam -name prime256v1 -genkey -noout | openssl ec -outform DER 2>/dev/null \
  | openssl base64 -A | tr '+/' '-_' | tr -d '=' > "$f"
az keyvault secret set --vault-name "$KV" --name vapid-private-key --file "$f" --output none

rm -f "$f"
```

| Secret | Env var | Value |
|---|---|---|
| `cookie-key` | `BW_COOKIE_KEY` | At least 32 bytes (above: 64 base64 characters) |
| `vault-keys` | `BW_VAULT_KEYS` | `kid:key[,kid:key]`; set `vault_primary_kid` (`BW_VAULT_PRIMARY`) once the ring holds two keys |
| `vapid-private-key` | `BW_NOTIFY_VAPID_PRIVATE_KEY` | P-256 key. `BW_NOTIFY_VAPID_SUBJECT` (`mailto:<alert_email>`) is set only while this is wired, because the app refuses one without the other |
| `portfolio-dispatch-token` | `BW_PUBLISH_PORTFOLIO_TOKEN` | Fine-grained PAT, portfolio repository only, Actions read/write (owner step below) |
| `linkedin-client-secret` | `BW_LINKEDIN_CLIENT_SECRET` | From the LinkedIn developer app; set `linkedin_client_id` too |
| `easyauth-client-secret` | (Easy Auth only) | Only for the `easyauth_credential = "client_secret"` fallback |

### 4. Second apply: wire the secrets

List the secrets you set, still with `http_health_probes_enabled = false`, then plan and apply
as in step 2:

```hcl
key_vault_secrets_set = ["cookie-key", "vault-keys", "vapid-private-key"]
```

A Key Vault reference to a secret that doesn't exist makes the revision fail to provision,
which is why the list is explicit. Output `key_vault_secrets_not_wired` shows what's left.

### 5. Deploy the image

```bash
ACR=$(terraform output -raw acr_name)
REPO=$(terraform output -raw container_image_repository)       # blog-writer-v2
SHA=$(git rev-parse --short=12 HEAD)
az acr build --registry "$ACR" --image "$REPO:$SHA" --file <v2 Dockerfile> \
  --build-arg BW_BUILD_SHA="$SHA" .
az containerapp update -n "$(terraform output -raw container_app_name)" \
  -g "$(terraform output -raw resource_group_name)" \
  --image "$(terraform output -raw acr_login_server)/$REPO:$SHA"
az containerapp revision list -n ca-blog-writer-v2 -g rg-blog-writer-dev \
  --query "[].{name:name, active:properties.active, health:properties.healthState}" -o table
```

- Terraform ignores `template[0].container[0].image`, so later applies keep the deployed image.
- **Terraform owns every environment variable.** Bake `BW_BUILD_SHA` into the image (a build
  argument turned into `ENV`); `az containerapp update --set-env-vars` would be removed by the
  next apply. `BW_SPA_DIR` also comes from the image.
- The v2 repository lives in the shared registry; only `blog-writer-v2` is ever pushed to or
  purged.

### 6. Third apply: HTTP probes

Delete `http_health_probes_enabled = false` from `terraform.tfvars` (the default is `true`) and
apply. Readiness and startup probes switch to `GET /api/health` (excluded from Easy Auth);
liveness stays a TCP process check.

### 7. Smoke test

```bash
URL=$(terraform output -raw container_app_url)
curl -s -o /dev/null -w '%{http_code}\n' "$URL/api/health"     # 200, anonymous
curl -s -o /dev/null -w '%{http_code}\n' "$URL/"               # 302 to /.auth/login/aad
```

Then sign in with a browser (only your account is admitted) and use the owner CLI access below.

## Portfolio site in the same app (`site_hosts`)

The portfolio (jatinmadan.com, the `portfolio` repository) runs as a **second container in the
same replica** instead of on GitHub Pages:

```
internet -> ACA ingress -> Easy Auth sidecar -> app container (8080, Blog Writer)
                                                 |- Host = a site host -> gateway -> 127.0.0.1:3000 (portfolio, Next.js)
                                                 '- any other host    -> Blog Writer (owner only)
```

- **One app, one replica, two images.** Blog Writer's deploy (`scripts/deploy-v2.sh`) owns the
  `app` container's image, and the portfolio repository's deploy (`scripts/deploy-aca.sh` there)
  owns the `portfolio` container's image. Terraform owns everything else and ignores both images.
  Either deploy makes a new revision, which restarts both containers. Blog Writer is crash-only,
  and the portfolio is stateless (it renders from Cosmos DB).
- **The gateway** (`server/src/blogwriter/core/site.py`) is the outermost app middleware after
  logging:
  - It serves only GET and HEAD, and forwards headers from an allowlist (no cookies,
    `Authorization`, `X-MS-*` or any other `X-*`). It never forwards `/_bw/*`.
  - `www.` redirects to the apex. `/studio` redirects to Blog Writer's own origin.
  - The portfolio listens on 127.0.0.1 only, so nothing else in the environment reaches it.
- **Publishing** (`BW_PUBLISH_SITE_UPSTREAM`): the publish job upserts `published-blogs`, then
  calls `POST 127.0.0.1:3000/_bw/revalidate` and reads `/blog/<slug>` back until the
  `bw-updated-at` marker shows the new version. There is no GitHub build, dispatch or PAT;
  `portfolio-dispatch-token` is no longer needed.
- **Easy Auth runs `AllowAnonymous`** because the site hosts are public. The app is then the
  only gate on Blog Writer's hosts. It always enforced owner-only access itself; Easy Auth was
  the outer layer.
  - The app sends unauthenticated browser navigations to `/.auth/login/aad`. Every other
    unauthenticated request gets 401.
  - The sidecar still strips client-sent `X-MS-CLIENT-PRINCIPAL*` headers and still refuses
    signed-in non-owners.
  - `checks.tf` asserts this mode only together with `BW_SITE_HOSTS`.
- **Identity:** the portfolio container uses the `portfolio_build` identity, which only has
  Cosmos Data Reader on `published-blogs` and Storage Blob Data Reader on `published`.
- **Sizing:** app 1 vCPU / 2 GiB plus portfolio 0.25 vCPU / 0.5 GiB, for a replica total of
  1.25 / 2.5 GiB.

Turning it on: set `site_hosts = ["jatinmadan.com", "www.jatinmadan.com"]` and
`portfolio_container_image = "<acr>/portfolio:<tag>"` (a first image pushed by the portfolio
repository), then apply. Then bind the domains with `scripts/bind-site-domains.sh`, which also
prints the DNS records:
1. Add the two `asuid` TXT records (no traffic impact) and run the script. It adds the host names
   without certificates, and you can test with `curl -k --resolve jatinmadan.com:443:<env IP>`.
2. Point the apex A record at the environment IP and `www` at the app FQDN, then run the script
   again to issue and bind the managed certificates.
3. Retire GitHub Pages in the portfolio repository settings.

## Owner CLI access (live testing)

With `easyauth_cli_access_enabled = true` (default) the app registration has the identifier URI
`api://<client id>`, a delegated `user_impersonation` scope pre-authorized for the Azure CLI,
and v2 access tokens; Easy Auth accepts both `api://<client id>` and `<client id>` as audience.
"Assignment required" and `allowedPrincipals` (owner only) still apply.

```bash
CID=$(terraform output -raw easyauth_client_id)
URL=$(terraform output -raw container_app_url)
TOKEN=$(az account get-access-token --resource "api://$CID" --query accessToken -o tsv)
curl -sS -H "Authorization: Bearer $TOKEN" "$URL/api/me"
```

- `GET`/`HEAD` work with the bearer token alone. Other methods also need the app's CSRF proof:
  keep the `__Host-bw` cookie from a previous response (`curl -c jar -b jar`) and send
  `Sec-Fetch-Site: same-origin`.
- `AADSTS50105`: you are not assigned to the enterprise app. `AADSTS65001`: the CLI
  pre-authorization is missing. If the CLI needs interaction, run
  `az login --scope "api://$CID/.default"` first.
- Never paste the token anywhere; it is valid for about an hour.

## Owner steps that remain

1. **Web IQ:** in the Web IQ Developer Portal, bind the **app identity's client ID**
   (`terraform output identity_client_ids`, key `app`). Managed-identity binding only; never
   create an `x-apikey`. The endpoint: if the account returned one under a known property name,
   the app already gets it as `BW_RESEARCH_WEBIQ_ENDPOINT` (output `webiq_endpoint`). The
   `2026-06-01-preview` response shape is not documented, so it may be `null`: check
   `terraform output webiq_properties` or the portal, set `webiq_endpoint` and apply. Until
   then the variable is not set and research uses `web_search` alone.
2. **LinkedIn:** add the app's OAuth callback on the v2 origin (`public_origin` + the callback
   path of the LinkedIn package) to the developer app's redirect URIs, set
   `linkedin_client_id`, store `linkedin-client-secret` and list it. At cutover the origin
   becomes the custom domain, so add that redirect URI too.
3. **Portfolio PAT:** create a fine-grained PAT for the portfolio repository only, with Actions
   read/write and an expiry you track; store it as `portfolio-dispatch-token` and list it. The
   app dispatches `portfolio_workflow` (default `deploy.yml`) on `portfolio_branch`.
4. **Portfolio build sign-in** (portfolio PR A): `azure/login` with
   `identity_client_ids.portfolio_build`; the job must not declare a GitHub environment (the
   federated subject is `repo:<owner>/<portfolio>:ref:refs/heads/<branch>`). It can read only
   the `published-blogs` container and the `published` blob container.
5. **Lab governance:** what happens when `Expiry Date` passes, and the longest expiry allowed.

## Alerts (`monitoring.tf`)

The environment logs to `law-blog-writer-dev` through the `log-analytics` destination:
`ContainerAppConsoleLogs_CL` (`Log_s` holds one raw stdout line) and
`ContainerAppSystemLogs_CL` (`Reason_s`, `Log_s`). Every rule filters on
`ContainerAppName_s == "ca-blog-writer-v2"` and parses the app's JSON lines with
`parse_json(Log_s)`. Only event names, ids and codes are projected. The action group emails
`alert_email`.

| Rule | Fires on | Mode |
|---|---|---|
| `alert-blogwriter-v2-alert-event` | any `alert_event` (`alert_kind`, `ref_id`): job_failed, publish_failed, needs_attention, quarantined, brake_tripped, notification_failed, test | every 5 min, stateless |
| `alert-blogwriter-v2-job-failures` | `job_settled` with `outcome == "failed"`, `job_execution_crashed`, `job_settle_failed`, `job_quarantine_alert_failed`, `schedule_fire_crashed`, `schedule_tick_crashed` (quarantines come through `alert_event`) | every 5 min, stateless |
| `alert-blogwriter-v2-restarts` | system log: `StoppingContainer` "will be restarted", non-zero exit codes, `OOMKilled`, `ContainerBackOff`, `RevisionFailure`, `ContainerAppFailure`; app log: `jobs_safe_mode`, `jobs_boot_log_unavailable` (crash-loop guard) | every 5 min, stateless |
| `alert-blogwriter-v2-scheduler-silent` | no `schedule_tick` for 15 minutes | stateful; **disabled by default** |

- Event rules select records by `ingestion_time()` over the last 6 minutes (5-minute schedule
  plus a minute of overlap), so late-arriving lines are still seen (measured latency here:
  p99 about 30 s). An event in the overlap can be emailed twice.
- **Scheduler alert:** the scheduler logs `schedule_tick` only for ticks that fired, parked or
  failed something, so an idle app looks silent. Set `scheduler_tick_alert_enabled = true` once
  every tick logs `schedule_tick`; the `scheduler_tick_alert` check reminds you until then.
- Test: trigger the app's "send test alert" (`emit_alert("test", ...)`); expect an email within
  about 10 minutes.
- The budget (`enable_budget`, default on) is advisory and scoped to the **whole shared resource
  group** (v1 included): 50% and 90% of actual spend and a 100% forecast. The Claude Marketplace
  invoice is billed separately.

## CI notes (deploy.yml, infra.yml)

- GitHub environments `deploy` and `infra`; store
  `AZURE_CLIENT_ID` (`identity_client_ids.ci_deploy` / `.ci_infra`), `AZURE_TENANT_ID` and
  `AZURE_SUBSCRIPTION_ID` as **environment secrets** so GitHub masks them. Subjects:
  `github_oidc_subjects` output.
- **deploy (ci_deploy):** AcrPush and Container Registry Tasks Contributor on the **shared**
  registry (needed for `az acr build`) and Container Apps Contributor on the v2 app only. Both
  registry roles cover every repository in it (the other app's included, and the purge task),
  and the app role also covers scale, ingress and the Easy Auth config. deploy must only run
  `az acr build` for `blog-writer-v2` and `az containerapp update --image`; limit the `deploy`
  environment to `main`. If the CLI ever needs to read the environment, add Reader on it.
- **infra (ci_infra):** Contributor on the **shared** resource group, which is in effect admin
  access to v1, the state account, the shared registry and the other app in it. It lists the
  state account key, which is how the key-based backend authenticates in CI (`ARM_USE_OIDC=true`
  plus `backend.hcl` values). It cannot write role assignments or locks: apply those locally,
  or set `ci_infra_rbac_admin_enabled = true` (RBAC Administrator with an ABAC condition limited
  to this configuration's roles and its own identities plus the owner).
- **Graph:** the Entra objects live in `infra/v2/entra` (own state key
  `blog-writer-v2-entra.tfstate`), applied by the owner only. This root and the data root have
  no azuread resources, so infra.yml plans and applies them with the ci_infra identity alone.
- **Environments:** run `scripts/setup-github-environments.sh` (after `az login` and
  `gh auth login`) whenever an identity, a tfvars file or the app name changes. It creates
  `deploy` (main only) and `infra` and sets their variables and secrets from the Terraform
  outputs and the local tfvars files (`TF_VARS_FILE`, `TF_VARS_FILE_PLATFORM`).
- **Deploy order:** deploy.yml runs only after CI succeeded on `main` for that exact commit
  (`workflow_run`), or by manual dispatch on `main` (rollbacks). It only changes the image;
  every setting comes from Terraform, so a deploy can never undo a Terraform change.
- Fail infra.yml when an **invariant** check fails; `health_probes_mode`,
  `scheduler_tick_alert` and `lab_expiry_tag` are reminders:
  ```bash
  terraform plan -input=false -out=tfplan
  terraform show -json tfplan | jq -e '[.checks[]? | select(.address.kind == "check"
    and (.status == "fail" or .status == "error")) | .address.name]
    - ["health_probes_mode", "scheduler_tick_alert", "lab_expiry_tag"] | length == 0'
  rm -f tfplan
  ```

## Invariant checks (`checks.tf`)

- `easy_auth`: planned and live config require auth (`RedirectToLoginPage`), exclude exactly
  `/api/health`, keep the token store off and admit only the owner.
- `single_replica`, `consumption_only_environment` (no workload profile on the app; the live
  environment has no dedicated profiles and no VNet), `app_fqdn` (the computed
  `<app>.<environment default domain>` behind `BW_PUBLIC_ORIGIN` and the redirect URIs matches
  the real FQDN).
- `storage_shared_key_disabled`, `cosmos_local_auth_disabled` (live read of the data-root
  account), `foundry_local_auth_disabled`.
- Reminders: `health_probes_mode`, `scheduler_tick_alert`, `lab_expiry_tag`.

## Feature flags

| Variable | Default | Notes |
|---|---|---|
| `easyauth_cli_access_enabled` | `true` | Owner bearer tokens from the Azure CLI. Off disables the scope and drops the pre-authorization. |
| `easyauth_credential` | `federated` | Sentinel secret `override-use-mi-fic-assertion-client-id` (as in `Azure-Samples/containerapps-builtinauth-bicep`). Fallback: `client_secret`. |
| `http_health_probes_enabled` | `true` | `false` only while the placeholder image runs (runbook). |
| `enable_claude_deployment` | `true` | Opus 5.5, format `Anthropic`, version `2` (Hosted on Azure), GlobalStandard 200. |
| `enable_webiq` | `true` | Unverified body (preview, no embedded schema). |
| `scheduler_tick_alert_enabled` | `false` | See Alerts. |
| `enable_budget`, `enable_locks`, `acr_purge_enabled`, `owner_data_access_enabled` | `true` | |
| `ci_infra_rbac_admin_enabled`, `enable_gpt_5_6_sol`, `enable_image_generation` | `false` | The app has no image settings yet. |
| `foundry_project_enabled` | `true` | Turning it off later forces a new Foundry account. |

Model versions (verified with `az cognitiveservices model list --location eastus`):

| Deployment | Version | Capacity | App setting |
|---|---|---|---|
| `gpt-6.2-sol` | `2026-11-17` | 500 | `BW_AI_DEPLOYMENT_SOL` |
| `gpt-6.2-astra` | `2026-11-17` | 300 | `BW_AI_DEPLOYMENT_ASTRA` |
| `gpt-6-luna` | `2026-09-22` | 200 | `BW_AI_DEPLOYMENT_LUNA` |
| `text-embedding-3-large` | `1` | 150 | `BW_AI_DEPLOYMENT_EMBEDDING` |
| `claude-opus-5-5` | `2` | 200 | `BW_AI_DEPLOYMENT_OPUS` |
| `gpt-5.6-sol` (opt-in) | `2026-07-09` | 100 | via `BW_AI_MODEL_OVERRIDES` |

Deployments use `version_upgrade_option = "OnceCurrentVersionExpired"`; bump versions with
`model_version_overrides` and capacity with `model_capacity`.

## App settings

Terraform sets (output `app_env_names`): `AZURE_CLIENT_ID` (app identity), `BW_ENV=prod`,
`BW_OWNER_OID`, `BW_PUBLIC_ORIGIN` (default `https://<app>.<environment default domain>`,
`public_origin` overrides), `BW_COSMOS_ENDPOINT`, `BW_COSMOS_DATABASE`,
`BW_AI_OPENAI_BASE_URL`, `BW_AI_ANTHROPIC_RESOURCE`, `BW_AI_DEPLOYMENT_*`,
`BW_STORAGE_BLOB_ENDPOINT`, `BW_STORAGE_CONTAINER_MEDIA` / `_PUBLISHED` / `_BACKUPS`,
`BW_PUBLISH_PORTFOLIO_REPOSITORY` / `_REF` / `_WORKFLOW`, `BW_PUBLISH_SITE_ORIGIN`, and when
configured `BW_NOTIFY_VAPID_SUBJECT`, `BW_LINKEDIN_CLIENT_ID`, `BW_VAULT_PRIMARY`,
`BW_RESEARCH_WEBIQ_ENDPOINT`; plus the Key Vault references above. Not set here:
`BW_BUILD_SHA` and `BW_SPA_DIR` (image).

## Operational notes

- **Locks.** CanNotDelete locks on storage, Foundry and Key Vault also block deleting their
  child resources and the role assignments scoped to them. The data root's lock on the Cosmos
  DB account blocks deleting the two Cosmos DB SQL role assignments made here (app,
  portfolio build): lift it in `infra/v2/data` first when an identity is replaced or this root is
  destroyed. Set `enable_locks = false`, apply as owner, change, then re-enable.
- **Destroy** removes only what this root created; the shared resources are data sources. Key
  Vault and Foundry names stay reserved while soft-deleted (`name_suffix` picks new ones).
- **Names.** Globally unique names use a stable 6-character hash of the subscription and
  resource group.
- **Cutover (later).** Bind the custom domain to `ca-blog-writer-v2` (CNAME and `asuid` TXT,
  managed certificate; prefer `azurerm_container_app_custom_domain` here over CLI binding), set
  `custom_domain_hostname` and `public_origin = "https://<custom domain>"`, update the LinkedIn
  redirect URI, then retire v1.
