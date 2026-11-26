> **Status (2026-11-26).** Only the **data root** (`infra/v2/data/`) has been applied. It created Cosmos DB
> `cosmos-blogwriter-v2-7c3167` in the existing `rg-blog-writer-dev`.
>
> The owner decided to reuse the existing app (`ca-blog-writer-dev`, custom domain `writer.jatinmadan.com`),
> environment, ACR and resource group. **This platform root still assumes a new resource group, a new ACR and a
> new Cosmos account. Do not apply it** until it is refactored to:
> - use those existing resources as data sources or imports;
> - read Cosmos from the data root;
> - create only the extra resources the owner approves.

# Blog Writer v2 infrastructure (`infra/v2`)

Terraform for the v2 platform: one always-on Azure Container App behind Easy Auth, a
dedicated Cosmos DB account, private Blob storage, Key Vault, Microsoft Foundry and the CI
identities. Everything lives in its own resource group (`rg-blog-writer-v2`, East US).

v1 (`infra/terraform`) stays untouched until cutover. Its resource group, ACR and Cosmos
account are shared with other projects, so nothing here references them. **Never run a v1
apply.**

This repository is **public**. Real values (subscription, tenant and object IDs, email
addresses, names) only go in `terraform.tfvars`, `backend.hcl` (both gitignored) or GitHub
environment secrets.

## Layout

| Path | Purpose |
|---|---|
| `bootstrap/` | Tiny root with **local** state. It creates the state resource group, a StorageV2 account (shared key off, HTTPS only, TLS 1.2, versioning and soft delete) and the `tfstate` container. It grants the deployer Storage Blob Data Contributor on the container and prints the backend config. |
| `*.tf` (this folder) | Main root. azurerm `~> 4.81`, azapi `~> 2.14`, azuread `~> 3.10` (optional app registration), time `~> 0.14`. The backend is `azurerm` with `use_azuread_auth = true`, configured with `-backend-config=backend.hcl`. |
| `terraform.tfvars.example`, `backend.hcl.example` | Committed placeholders. Copy them to the gitignored real files. |

| File | Contents |
|---|---|
| `main.tf` | Resource group, Log Analytics, the five user-assigned identities, Container Apps environment and its diagnostic settings |
| `containerapp.tf` | The container app: env vars, Key Vault references, probes |
| `easyauth.tf` | The `authConfigs/current` resource (azapi) and the optional Entra app (azuread) |
| `cosmos.tf` | Cosmos DB account, the `blog-writer` database, every v2 container |
| `storage.tf` | Storage account, containers and lifecycle rules |
| `foundry.tf` | Foundry account and project, model deployments, opt-in Claude deployment and image account |
| `keyvault.tf`, `acr.tf`, `webiq.tf` | Key Vault; ACR and its purge task; Web IQ (opt-in) |
| `rbac.tf`, `github_oidc.tf` | Every role assignment; GitHub federated credentials |
| `monitoring.tf`, `locks.tf`, `checks.tf` | Action group and budget; locks; invariant checks |

## Who provisions what

| Item | Terraform | Owner |
|---|---|---|
| Resource groups (state and app), with the lab tags | ✓ | |
| Log Analytics (30 days), action group (email), advisory budget | ✓ | |
| Identities: `app`, `easyauth`, `portfolio_build`, `ci_deploy`, `ci_infra` | ✓ | |
| GitHub OIDC federated credentials | ✓ | |
| Key Vault (RBAC mode, purge protection) | ✓ | |
| Key Vault **secret values** | | ✓ (step 2) |
| Storage account, containers and lifecycle rules | ✓ | |
| Cosmos DB account, database, all containers, data-plane roles | ✓ | |
| ACR (Basic, admin off) and purge task | ✓ | |
| Foundry account and project; GPT-6.2 Sol and Astra, GPT-6 Luna, text-embedding-3-large | ✓ | |
| gpt-5.6-sol, gpt-image-2 account | opt-in flags | |
| **Claude Opus 5.5 deployment, Marketplace terms and quota** | opt-in, unverified | ✓ (step 3) |
| Container Apps environment (Consumption only), container app, Easy Auth config | ✓ | |
| **Entra app registration, federated credential, "Assignment required", your assignment** | opt-in (`create_entra_app`) | ✓ (step 1) |
| Web IQ account | opt-in (`enable_webiq`) | ✓ register the provider and bind the identity (step 4) |
| Container image | first revision only | deploy.yml |
| Custom domain and certificate | later | ✓ (step 6) |
| LinkedIn developer app settings | | ✓ |
| CanNotDelete locks | ✓ (`enable_locks`) | |

## How the lab policies are satisfied

| Policy | How |
|---|---|
| Allowed locations (no West US 3 or Sweden Central) | `location` and `image_location` are validated against the allowed list. The defaults are `eastus` and `eastus2` (images only). Web IQ and the action group are `global`, which is exempt. |
| Resource groups need `Purpose`, `Owner` and `Expiry Date` (deny) | Required variables `tag_purpose`, `tag_owner` and `tag_expiry_date` (validated as `YYYY-MM-DD`). They go on both resource groups and on every taggable resource, after the app tags so they always win. The `lab_expiry_tag` check warns 30 days before expiry. |
| AI Services local auth disabled (modify/DINE) | `local_auth_enabled = false` on both Foundry accounts, plus a check block, so Terraform and the policy never fight. |
| Storage HTTPS only (deny) | `https_traffic_only_enabled = true` and `min_tls_version = "TLS1_2"` on both storage accounts. |
| Public blob access (audit) | `allow_nested_items_to_be_public = false` and private containers. The portfolio build reads `published` with Entra ID. |
| Only `BizTalk Contributor` is denied | Roles used: Foundry User, Storage Blob Data Contributor and Reader, Key Vault Secrets User and Officer, AcrPull, AcrPush, Container Registry Tasks Contributor, Container Apps Contributor, Contributor, and optionally Role Based Access Control Administrator. Cosmos DB uses its own built-in data roles. |

Resource providers are not registered by Terraform (`resource_provider_registrations = "none"`
and azapi `skip_provider_registration = true`). Every provider needed by default was
`Registered` on 2026-11-25, except `Microsoft.WebIQ`.

## Prerequisites

- Terraform >= 1.9 and Azure CLI, signed in to the lab subscription:
  `az login`, then `export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)`.
- The **first** applies run locally as the owner, who needs Owner (or Contributor + User Access
  Administrator) on the subscription. Role assignments and locks need
  `Microsoft.Authorization/*/write`, which the CI identities don't have (and they don't exist
  yet).

## Apply order

### 1. State (bootstrap)

```bash
cd infra/v2/bootstrap
cp terraform.tfvars.example terraform.tfvars    # fill in the three lab tags
terraform init
terraform apply
terraform output -raw backend_config > ../backend.hcl
```

The bootstrap state is a local `terraform.tfstate` (gitignored); back it up. Losing it only
means re-importing the resource group, the account, the container, the role assignments and
the lock.

The state account accepts Entra ID only. If the next `terraform init` returns 403, wait a few
minutes for the new Storage Blob Data Contributor assignment to propagate.

### 2. Main root, first apply

Register the Entra app first (owner step 1, items 1–2), so you have `easyauth_client_id`. Its
redirect URI, federated credential and assignment (items 1, 3 and 4) need outputs of this
apply, so finish them right after it.

```bash
cd infra/v2
cp terraform.tfvars.example terraform.tfvars    # owner OID, alert email, tags, GitHub owner, easyauth_client_id
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan                      # local only; never commit or upload plans
terraform apply tfplan
```

The first revision runs the Microsoft sample image `mcr.microsoft.com/dotnet/samples:aspnetapp`
(port 8080) with TCP readiness and startup probes (`http_health_probes_enabled = false`). Easy
Auth is attached in the same apply. The app is reachable without auth only for the few seconds
between the app and its auth config being created, and in that window it serves only the
sample page.

### 3. Owner steps 1–4 (below), then wire the secrets

List every Key Vault secret you have set in `key_vault_secrets_set`, then apply again. A Key
Vault reference to a secret that doesn't exist makes the revision fail to provision, which is
why the list is explicit.

### 4. CI

1. Give `ci_infra` access to the state: in `bootstrap/terraform.tfvars` set
   `state_contributor_principal_ids = { ci_infra = "<identity_principal_ids.ci_infra>" }` and
   apply the bootstrap root again.
2. Create the GitHub environments `deploy` and `infra` (approval required on `infra`). Add these
   as **environment secrets** rather than variables, so GitHub masks them in this public repo's
   logs: `AZURE_CLIENT_ID` (`identity_client_ids.ci_deploy` or `.ci_infra`),
   `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`. Terraform also reads `ARM_CLIENT_ID`,
   `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID`, `ARM_USE_OIDC=true` and the `TF_VAR_*` inputs.
3. Federated subjects (`github_oidc_subjects` output):
   - `repo:<owner>/<repo>:environment:deploy` for ci_deploy
   - `repo:<owner>/<repo>:environment:infra` for ci_infra (the plan and apply jobs both run in
     the `infra` environment)
   - `repo:<owner>/<portfolio>:ref:refs/heads/master` for the portfolio build. The portfolio job
     that signs in **must not declare an environment** (for example `github-pages`), or the
     subject becomes `environment:<name>` and sign-in fails. That identity has data-plane roles
     only, so use `azure/login` with `allow-no-subscriptions: true` or `@azure/identity`
     directly.
4. **deploy.yml** (ci_deploy):
   - `az acr build --registry <acr_name> --image <container_image_repository>:<sha> .`, then
     `az containerapp update -n <container_app_name> -g <resource_group_name> --image <acr_login_server>/<container_image_repository>:<sha>`.
   - AcrPush can't queue ACR builds, so ci_deploy also has **Container Registry Tasks
     Contributor** on the registry.
   - Container Apps Contributor is scoped to the app only. If the CLI ever needs to read the
     environment, add Reader on the environment.
   - That role also covers the app's scale and ingress settings and its `authConfigs` child
     (Easy Auth), so ci_deploy *could* drop auth or raise `max_replicas`. deploy.yml must only
     run `az containerapp update --image`. Limit the `deploy` environment to the `main` branch
     and keep the post-deploy smoke check (`/` and `/api/settings` return 302/401 without a
     session). The next infra plan's live `easy_auth` check also reports any drift.
5. **infra.yml** (ci_infra):
   - Contributor on the resource group can plan everything and apply most changes.
   - It **cannot** write role assignments or locks. For RBAC changes, either apply locally as
     the owner, or set `ci_infra_rbac_admin_enabled = true`. That grants Role Based Access
     Control Administrator with an ABAC condition limited to this configuration's roles,
     assigned to its own identities and the owner (never to ci_infra itself). Locks always need
     an owner apply.
   - The azuread provider is configured in every run: it gets a Graph token but makes no Graph
     calls unless `create_entra_app = true`. Leave that false for CI, because the CI identities
     have no Graph permissions.
   - Contributor on this resource group is, in effect, access to all of v2's data: it can add
     federated credentials to any identity here (including `app`, which reads Key Vault, Cosmos
     DB and Blob) and write Cosmos DB SQL role assignments. Treat the `infra` environment as
     admin access. Keep required reviewers on it, and note they gate the plan job too: a plan
     runs the pull request's Terraform with this identity, so review the diff before approving.
   - Fail the job when an **invariant** check fails. `health_probes_mode` and `lab_expiry_tag`
     are reminders that fail by design for a while, so don't grep for
     `Check block assertion failed`. Read the check statuses instead:
     ```bash
     terraform plan -input=false -out=tfplan
     terraform show -json tfplan | jq -e '[.checks[]? | select(.address.kind == "check"
       and (.status == "fail" or .status == "error")) | .address.name]
       - ["health_probes_mode", "lab_expiry_tag"] | length == 0'
     rm -f tfplan
     ```
   - Upload no plan files or artifacts.

### 5. First real image

Once deploy.yml has rolled out an image that serves `GET /api/health` on port 8080, set
`http_health_probes_enabled = true` and apply. Readiness and startup probes switch to HTTP
`/api/health`; liveness stays a TCP (process) check. Terraform ignores
`template[0].container[0].image`, so this and every later apply keeps the deployed image
instead of rolling back to the placeholder.

## Owner steps

### 1. Entra app registration (Easy Auth)

Skip if you set `create_entra_app = true`. That needs Graph rights and is for local applies
only.

1. Entra ID → App registrations → New registration. Use single tenant, platform **Web**, and the
   redirect URI from output `easyauth_redirect_uris`
   (`https://<container_app_fqdn>/.auth/login/aad/callback`; add it after the first apply). Under
   Authentication, tick **ID tokens**.
2. Put the Application (client) ID in `easyauth_client_id`.
3. Federated credential (`easyauth_credential = "federated"`, the default). Use the values from
   output `easyauth_federated_credential`:
   ```bash
   az ad app federated-credential create --id <client-id> --parameters '{
     "name": "easyauth-managed-identity",
     "issuer": "https://login.microsoftonline.com/<tenant-id>/v2.0",
     "subject": "<easyauth identity principal id>",
     "audiences": ["api://AzureADTokenExchange"]
   }'
   ```
   The container app holds the secret `override-use-mi-fic-assertion-client-id`, which contains
   the **easyauth identity's client ID** (not a secret). The Easy Auth sidecar recognizes that
   name and signs in with the managed identity instead of a client secret.
4. Enterprise applications → the app → Properties → **Assignment required = Yes**. Then Users
   and groups → assign only yourself.
5. **Fallback** if the federated credential fails: create a client secret, store it as Key Vault
   secret `easyauth-client-secret`, add that name to `key_vault_secrets_set`, set
   `easyauth_credential = "client_secret"`, and apply. Track the secret's expiry.

`allowedPrincipals.identities = [owner_object_id]` and the in-app `OWNER_OID` check back up
the assignment.

### 2. Key Vault secret values

The vault is in RBAC mode, and Terraform grants you **Key Vault Secrets Officer**. Set each value
once. Values never pass through Terraform, CI or state, and the app reads them through
versionless references.

```bash
KV=$(terraform output -raw key_vault_name)
umask 077; f=$(mktemp)
openssl rand -base64 32 | tr -d '\n' > "$f"
az keyvault secret set --vault-name "$KV" --name app-encryption-key --file "$f"
rm -f "$f"
```

| Secret | Env var | Value |
|---|---|---|
| `app-encryption-key` | `APP_ENCRYPTION_KEY` | 32 random bytes, base64 |
| `vapid-private-key` / `vapid-public-key` | `VAPID_PRIVATE_KEY` / `VAPID_PUBLIC_KEY` | A Web Push VAPID key pair |
| `portfolio-dispatch-token` | `PORTFOLIO_DISPATCH_TOKEN` | Fine-grained PAT, portfolio repo only, Actions read/write |
| `linkedin-client-secret` | `LINKEDIN_CLIENT_SECRET` | From the LinkedIn developer app (Phase 5) |
| `easyauth-client-secret` | (auth config only) | Only for the client-secret fallback |

Use `--file` rather than `--value`, so secrets stay out of your shell history. Then list the
names you set in `key_vault_secrets_set` and apply (output `key_vault_secrets_not_wired` shows
what's left).

### 3. Claude Opus 5.5 deployment (Marketplace)

Claude is sold through the Azure Marketplace. The first deployment has to accept the offer
terms in the Foundry portal, and the corporate private marketplace may block it.

1. Foundry portal → the project on account `foundry_account_name` → Discover → Models →
   `claude-opus-5-5` → Deploy → **Custom settings**.
2. Accept the Marketplace terms. Set the deployment name to **`claude-opus-5-5`** (it must equal
   `claude_deployment_name`), pick the **Hosted on Azure** version (`2`), and choose Global
   Standard.
3. Check the quota (default about 40 RPM, 40K input TPM, 8K output TPM) and file the increase
   request. Its approval is the Phase 4 go/no-go.

The app reaches it with `ANTHROPIC_FOUNDRY_RESOURCE` (resource `https://<foundry>.services.ai.azure.com/anthropic/`), an
Entra token for `https://ai.azure.com/.default` and the Foundry User role. No API key is
involved.

`enable_claude_deployment = true` (default false) manages the same deployment from Terraform:
format `Anthropic`, version `2`, GlobalStandard. That schema matches
`az cognitiveservices model list`, but an ARM-only create after the offer is accepted has **not
been verified**. To adopt a portal-created deployment, import it first:
`terraform import 'azurerm_cognitive_deployment.claude[0]' <deployment resource id>`.

### 4. Web IQ

1. `az provider register --namespace Microsoft.WebIQ`, then wait for
   `az provider show -n Microsoft.WebIQ --query registrationState` to return `Registered`.
2. Set `enable_webiq = true` and apply **locally** first. The preview API
   `2026-06-01-preview` isn't in azapi's embedded schemas, so body validation is off. Kind
   `WebIQ.Grounding`, SKU `G3` and location `global` come from the plan; the empty
   `properties` object is unverified.
3. In the Web IQ Developer Portal, bind the **app identity's client ID**
   (`identity_client_ids.app`). Use managed-identity binding only and never create an
   `x-apikey`. The app sees `WEBIQ_ENABLED=true`.

### 5. Lab governance

- Find out what happens when `Expiry Date` passes and the longest expiry the lab allows.
- Confirm who in the lab tenant has admin access to v2's data.

### 6. Custom domain (Phase 5)

The app moves to `writer.<your-domain>` (today bound to v1) so the LinkedIn redirect URI never
changes.

1. Create the CNAME `writer` → `container_app_fqdn`, and the TXT `asuid.writer` → the app's
   custom domain verification ID.
2. Add the hostname with a managed certificate. Prefer adding
   `azurerm_container_app_custom_domain` and `azurerm_container_app_environment_managed_certificate`
   here over out-of-band CLI binding, so Terraform owns it.
3. Set `custom_domain_hostname`, and add `https://writer.<your-domain>/.auth/login/aad/callback`
   to the Entra app.

## Feature flags and certainty

| Variable | Default | Notes |
|---|---|---|
| `easyauth_credential` | `federated` | The sentinel secret `override-use-mi-fic-assertion-client-id` is what `Azure-Samples/containerapps-builtinauth-bicep` uses, and community tests confirmed it (microsoft/azure-container-apps#1693). The Container Apps docs don't cover it yet. Fallback: `client_secret`. |
| `http_health_probes_enabled` | `false` | Flip after the first real deploy. A check warns until you do. |
| `create_entra_app` | `false` | azuread; owner-local applies only. |
| `enable_gpt_5_6_sol` | `false` | Research fallback if Phase 0 needs it. |
| `enable_image_generation` | `false` | Second AIServices account in `eastus2` with `gpt-image-2` `2026-04-21`. |
| `enable_claude_deployment` | `false` | **Unverified** ARM path (see owner step 3). |
| `enable_webiq` | `false` | **Unverified** body (preview, no embedded schema). |
| `foundry_project_enabled` | `true` | Needed for the Foundry portal. Turning it off later forces a new account. |
| `acr_purge_enabled` | `true` | `azurerm_container_registry_task` with an encoded `acr purge` step: tags older than 30 days, keeping the newest 10, plus untagged manifests. The task YAML hasn't run yet; check the first run with `az acr task logs`. |
| `ci_infra_rbac_admin_enabled` | `false` | The ABAC condition (role and principal GUID lists) follows Microsoft's delegation examples. Check the first assignment in the portal. |
| `enable_locks` | `true` | Locks on Cosmos DB, storage, Foundry and Key Vault. |
| `owner_data_access_enabled` | `true` | Owner data access for migration, the v1 export and restore drills. |

Model versions were verified with `az cognitiveservices model list` on 2026-11-25:

| Model | Version | Default capacity (K TPM) | Inference retirement |
|---|---|---|---|
| `gpt-6.2-sol` | `2026-11-17` | 500 | 2027-11-17 |
| `gpt-6.2-astra` | `2026-11-17` | 300 | 2027-11-17 |
| `gpt-6-luna` | `2026-09-22` | 200 | 2028-03-11 |
| `text-embedding-3-large` | `1` | 150 | 2028-02-09 |
| `gpt-5.6-sol` (opt-in) | `2026-07-09` | 100 | 2028-01-11 |

Deployments use `version_upgrade_option = "OnceCurrentVersionExpired"`. Bump versions on
purpose with `model_version_overrides`, and tune capacity with `model_capacity`.

## Data (Cosmos DB `blog-writer`)

Every container uses partition key version 2. Every indexing policy includes `/*` and excludes
`/embedding/*`, `/markdown/*`, `/htmlContent/*` and `/mdxContent/*`.

| Container | Partition key | TTL | Extra excluded paths |
|---|---|---|---|
| settings, stories, ideas, research, articles, sources | `/id` | off | |
| posts | `/id` | off | `/payload/*` |
| article-versions | `/articleId` | off | |
| signals | `/sourceId` | 90 days | |
| jobs | `/id` | `-1` (per-item) | `/events/*` |
| usage | `/month` | 396 days (about 13 months) | |
| engagement | `/id` | `-1` (per-item; 48 h on third-party text) | |
| ephemeral | `/id` | 1 day default; items set shorter | |
| published-blogs, published-blogs-staging | `/id` (**`id == slug` contract**) | off | |

TTL is only garbage collection. The app still checks `expiresAt`.

| Principal | Cosmos DB role | Scope |
|---|---|---|
| App | Built-in Data Contributor | The database |
| Portfolio build | Built-in Data Reader | `published-blogs` and `published-blogs-staging` only |
| Owner (optional) | Built-in Data Contributor | The database |

The account is serverless, with `Continuous7Days` backup, local auth disabled and TLS 1.2.
Storage lifecycle rules:

- Blobs under `backups/daily/` are deleted after 35 days. Write the daily dumps there; the
  `backups/v1-export/` migration input is not touched.
- Previous versions and snapshots are deleted after 30 days.
- Soft-deleted blobs and containers are purged after 30 days.

## Invariant checks (`checks.tf`)

Check blocks run on every plan and apply, and report warnings.

- **`easy_auth`**: the planned config and a live read of `authConfigs/current` must both have
  auth enabled, `RedirectToLoginPage`, exactly `["/api/health"]` excluded, the token store off
  and only the owner allowed.
- **`single_replica`**: a single revision with `min = max = 1` replica.
- **`consumption_only_environment`**: only the Consumption profile, with no VNet.
- **`acr_admin_disabled`**, **`storage_shared_key_disabled`**, **`cosmos_local_auth_disabled`**
  and **`foundry_local_auth_disabled`**.
- **`published_blogs_contract`**: the partition key stays `/id`.
- **`health_probes_mode`** and **`lab_expiry_tag`**: reminders, not invariants. They fail by
  design until you flip the probes or extend the expiry tag, so CI doesn't gate on them.

Container Apps has no `requireAuthentication` property; App Service does. "Require
authentication" here means `unauthenticatedClientAction` is not `AllowAnonymous`.

## Operational notes

- **Locks.** CanNotDelete locks also block deleting child and extension resources through
  ARM: Cosmos containers and SQL role assignments, model deployments, blob containers, and
  Azure role assignments scoped to a locked resource. Set `enable_locks = false`, apply as
  owner, make the change, then turn locks back on.
- **Logs.** The environment sends logs through diagnostic settings (`azure-monitor`
  destination) to the `ContainerAppConsoleLogs` and `ContainerAppSystemLogs` tables, so the
  workspace has local auth off. `ContainerAppHTTPLogs` is deliberately off, because platform
  access logs include OAuth callback query strings.
- **Alerts.** Log-alert rules (no `scheduler_tick` for 15 minutes, restart or OOM,
  `job_failed`/`publish_failed`/`needs_attention`, brake trips, failed notification receipts)
  are added in Phase 1 once the app's structured log fields exist. They target the action group
  created here.
- **Budget.** The budget is advisory: it only notifies, at 50% and 90% of actual spend and at a
  100% forecast. The Claude Marketplace invoice is billed separately, so reconcile both.
- **Names.** Globally unique names use a stable 6-character hash of the subscription and
  resource group. Set `name_suffix` if a soft-deleted Key Vault (90-day purge protection) or
  Foundry account blocks a re-create.
- **Restore.** `RESTORE.md` (Phase 9) covers point-in-time restore with `--disable-ttl`, then
  re-applying the data-plane role assignments and repointing `COSMOS_ENDPOINT`.
