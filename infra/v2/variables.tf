# All real values live in terraform.tfvars (gitignored) or TF_VAR_* in CI.
# This repository is public: never commit subscription/tenant/object IDs,
# email addresses or people's names. Resource NAMES of the existing footprint
# are fine as defaults.

# ---------------------------------------------------------------------------
# Subscription, regions, naming
# ---------------------------------------------------------------------------

variable "subscription_id" {
  description = "Target subscription ID. Leave null to use ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "location" {
  description = "Region of the resources this root creates (identities, Key Vault, storage, Foundry). The lab 'Allowed locations' policy only permits the regions in the validation list; the model set (GPT-6.2, Claude Opus 5.5) was verified as GlobalStandard in East US, where the existing environment also runs."
  type        = string
  default     = "eastus"

  validation {
    condition = contains([
      "eastus", "eastus2", "westus", "westus2", "centralus", "northcentralus",
      "southcentralus", "westcentralus", "francecentral", "westeurope", "southindia",
    ], var.location)
    error_message = "location must be a region allowed by the lab policy (East US is the default and the only region verified for the model set)."
  }
}

variable "image_location" {
  description = "Region of the optional image-generation Foundry account. gpt-image-2 is only offered in East US 2, which lacks GPT-6.2."
  type        = string
  default     = "eastus2"

  validation {
    condition = contains([
      "eastus", "eastus2", "westus", "westus2", "centralus", "northcentralus",
      "southcentralus", "westcentralus", "francecentral", "westeurope", "southindia",
    ], var.image_location)
    error_message = "image_location must be a region allowed by the lab policy."
  }
}

variable "name_prefix" {
  description = "Prefix for the names of resources this root creates: 3-14 lowercase letters, digits or hyphens, starting with a letter."
  type        = string
  default     = "blogwriter-v2"

  validation {
    # No "--": Key Vault names reject consecutive hyphens.
    condition     = can(regex("^[a-z][a-z0-9-]{1,12}[a-z0-9]$", var.name_prefix)) && !strcontains(var.name_prefix, "--")
    error_message = "name_prefix must be 3-14 lowercase letters, digits or hyphens, start with a letter, not end with a hyphen and not contain \"--\"."
  }
}

variable "name_suffix" {
  description = "Suffix for globally unique names (Key Vault, storage, Foundry, Web IQ). Null derives a stable 6-character hash of the subscription and resource group. Override (4-6 lowercase letters/digits) if a soft-deleted name blocks a re-create."
  type        = string
  default     = null

  validation {
    condition     = var.name_suffix == null || can(regex("^[a-z0-9]{4,6}$", var.name_suffix))
    error_message = "name_suffix must be 4-6 lowercase letters or digits."
  }
}

# ---------------------------------------------------------------------------
# Existing resources (read with data sources, never managed or changed)
# ---------------------------------------------------------------------------

variable "resource_group_name" {
  description = "EXISTING resource group, shared with v1, its Terraform state account and another app. This root creates resources in it but never manages or changes the group itself."
  type        = string
  default     = "rg-blog-writer-dev"
}

variable "container_app_environment_name" {
  description = "EXISTING Container Apps environment (legacy Consumption-only, shared with v1). The v2 app is added to it side by side."
  type        = string
  default     = "cae-blog-writer-dev"
}

variable "log_analytics_workspace_name" {
  description = "EXISTING Log Analytics workspace the environment logs to (ContainerAppConsoleLogs_CL / ContainerAppSystemLogs_CL). The alert rules query it."
  type        = string
  default     = "law-blog-writer-dev"
}

variable "container_registry_name" {
  description = "EXISTING container registry, shared with another app. Never changed (its admin user stays as it is); v2 pulls with the app identity (AcrPull) and pushes to its own repository."
  type        = string
  default     = "blogwriterdeveus6c2uw5"

  validation {
    condition     = can(regex("^[A-Za-z0-9]{5,50}$", var.container_registry_name))
    error_message = "container_registry_name must be 5-50 letters or digits."
  }
}

variable "cosmos_account_name" {
  description = "v2 Cosmos DB account created by infra/v2/data (output cosmos_account_name there). Read only; the data root owns it, its containers, the owner's role and its lock."
  type        = string
  default     = "cosmos-blogwriter-v2-7c3167"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,42}[a-z0-9]$", var.cosmos_account_name))
    error_message = "cosmos_account_name must be a valid Cosmos DB account name."
  }
}

variable "cosmos_database_name" {
  description = "v2 Cosmos DB database (created by infra/v2/data)."
  type        = string
  default     = "blog-writer"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$", var.cosmos_database_name))
    error_message = "cosmos_database_name must be 1-255 letters, digits, '.', '_' or '-', starting with a letter or digit."
  }
}

# ---------------------------------------------------------------------------
# Lab-required tags, applied (with the app tags) to every taggable resource
# this root creates.
# ---------------------------------------------------------------------------

variable "tag_purpose" {
  description = "Value of the lab-required 'Purpose' tag."
  type        = string

  validation {
    condition     = length(trimspace(var.tag_purpose)) > 0
    error_message = "tag_purpose must not be empty."
  }
}

variable "tag_owner" {
  description = "Value of the lab-required 'Owner' tag."
  type        = string

  validation {
    condition     = length(trimspace(var.tag_owner)) > 0
    error_message = "tag_owner must not be empty."
  }
}

variable "tag_expiry_date" {
  description = "Value of the lab-required 'Expiry Date' tag, formatted YYYY-MM-DD. A check block warns 30 days before it passes."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.tag_expiry_date)) && can(formatdate("YYYY-MM-DD", "${var.tag_expiry_date}T00:00:00Z"))
    error_message = "tag_expiry_date must be a valid date formatted YYYY-MM-DD."
  }
}

variable "extra_tags" {
  description = "Additional tags. The lab-required tags always win over keys given here."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Owner and alerting
# ---------------------------------------------------------------------------

variable "owner_object_id" {
  description = "Entra object ID of the owner: the only principal Easy Auth admits (allowedPrincipals) and BW_OWNER_OID for the in-app fail-closed check. Also gets Key Vault Secrets Officer to set secret values."
  type        = string

  validation {
    # Rejects the all-zero placeholder from terraform.tfvars.example, which
    # would otherwise fail mid-apply (PrincipalNotFound on the role assignments).
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.owner_object_id)) && var.owner_object_id != "00000000-0000-0000-0000-000000000000"
    error_message = "owner_object_id must be the owner's real Entra object ID (a GUID, not the all-zero placeholder)."
  }
}

variable "alert_email" {
  description = "Email address for the Azure Monitor action group and the budget notifications; also the VAPID contact (BW_NOTIFY_VAPID_SUBJECT = mailto:<this>)."
  type        = string

  validation {
    # Also what the app accepts in a mailto: VAPID subject.
    condition     = can(regex("^[^@\\s/]+@[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "owner_data_access_enabled" {
  description = "Grant the owner Storage Blob Data Contributor on the v2 storage account (v1 export, restore drills, debugging). The owner's Cosmos DB data role lives in infra/v2/data."
  type        = bool
  default     = true
}

variable "enable_budget" {
  description = "Create the advisory monthly budget on the (shared) resource group."
  type        = bool
  default     = true
}

variable "monthly_budget_amount" {
  description = "Advisory monthly budget for the whole shared resource group (v1, v2 and anything else in it), in the billing currency. Budgets only notify; they never stop resources. The Claude Marketplace invoice is billed separately."
  type        = number
  default     = 300

  validation {
    condition     = var.monthly_budget_amount > 0
    error_message = "monthly_budget_amount must be positive."
  }
}

variable "scheduler_tick_alert_enabled" {
  description = "Enable the 'no scheduler tick for 15 minutes' log alert. Off by default: the scheduler currently logs schedule_tick only for ticks that fired, parked or failed something, so an idle app would look silent. Turn on once every tick logs schedule_tick."
  type        = bool
  default     = false
}

variable "enable_locks" {
  description = "CanNotDelete locks on storage, Foundry and Key Vault (the Cosmos DB lock is in infra/v2/data). Creating or removing locks needs Owner or User Access Administrator (not the ci_infra identity)."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# GitHub OIDC (federated credentials; no client secrets anywhere)
# ---------------------------------------------------------------------------

variable "github_owner" {
  description = "GitHub user or organization that owns the Blog Writer and portfolio repositories. Case-sensitive: must match the 'sub' claim GitHub issues."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$", var.github_owner))
    error_message = "github_owner must be a valid GitHub user or organization name."
  }
}

variable "github_repo" {
  description = "Blog Writer repository name (case-sensitive)."
  type        = string
  default     = "Blog-Writer"
}

variable "github_deploy_environment" {
  description = "GitHub environment used by deploy.yml (federated subject repo:<owner>/<repo>:environment:<name>)."
  type        = string
  default     = "deploy"
}

variable "github_infra_environment" {
  description = "GitHub environment used by infra.yml for both plan and apply (federated subject repo:<owner>/<repo>:environment:<name>)."
  type        = string
  default     = "infra"
}

variable "portfolio_github_owner" {
  description = "Owner of the portfolio repository. Null means github_owner."
  type        = string
  default     = null
}

variable "portfolio_repo" {
  description = "Portfolio repository name (case-sensitive)."
  type        = string
  default     = "portfolio"
}

variable "portfolio_branch" {
  description = "Portfolio branch whose build may sign in (federated subject repo:<owner>/<portfolio>:ref:refs/heads/<branch>); also BW_PUBLISH_PORTFOLIO_REF."
  type        = string
  default     = "master"
}

variable "ci_infra_rbac_admin_enabled" {
  description = "Also grant the ci_infra identity 'Role Based Access Control Administrator' on the resource group, constrained by an ABAC condition to the roles and principals this configuration uses. Without it, infra.yml can plan everything but cannot apply role-assignment changes."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Easy Auth (Microsoft Entra ID in front of the container app)
# ---------------------------------------------------------------------------

variable "create_entra_app" {
  description = "Create the Entra app registration, its service principal (assignment required), the owner's assignment and the managed-identity federated credential with the azuread provider. Needs Graph rights (the owner applies it locally); the CI identities have none."
  type        = bool
  default     = true
}

variable "entra_app_display_name" {
  description = "Display name of the Entra app registration when create_entra_app = true."
  type        = string
  default     = "Blog Writer v2"
}

variable "easyauth_client_id" {
  description = "Application (client) ID of an owner-created Entra app registration. Only used (and then required) when create_entra_app = false."
  type        = string
  default     = null

  validation {
    condition     = var.easyauth_client_id == null || (can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.easyauth_client_id)) && var.easyauth_client_id != "00000000-0000-0000-0000-000000000000")
    error_message = "easyauth_client_id must be the app registration's client ID (a GUID, not the all-zero placeholder)."
  }
}

variable "easyauth_credential" {
  description = "How Easy Auth authenticates to Entra: 'federated' (the easyauth managed identity is a federated credential on the app registration; no secret) or 'client_secret' (client secret read from Key Vault secret 'easyauth-client-secret', which must then be listed in key_vault_secrets_set)."
  type        = string
  default     = "federated"

  validation {
    condition     = contains(["federated", "client_secret"], var.easyauth_credential)
    error_message = "easyauth_credential must be 'federated' or 'client_secret'."
  }
}

variable "easyauth_cli_access_enabled" {
  description = "Programmatic owner access for live testing: identifier URI api://<client id>, a delegated user_impersonation scope pre-authorized for the Azure CLI, v2 access tokens, and both audiences allowed by Easy Auth. Assignment required + allowedPrincipals still admit only the owner."
  type        = bool
  default     = true
}

variable "easyauth_cookie_expiration_days" {
  description = "Easy Auth session cookie lifetime (FixedTime convention). The plan calls for 7-14 days."
  type        = number
  default     = 14

  validation {
    condition     = var.easyauth_cookie_expiration_days >= 1 && var.easyauth_cookie_expiration_days <= 14 && floor(var.easyauth_cookie_expiration_days) == var.easyauth_cookie_expiration_days
    error_message = "easyauth_cookie_expiration_days must be a whole number between 1 and 14."
  }
}

variable "custom_domain_hostname" {
  description = "Optional later step (cutover): custom domain of the v2 app, e.g. writer.example.com. Only adds an Entra redirect URI (create_entra_app = true) and shows up in outputs; binding the domain and certificate is a separate step. Set public_origin to match."
  type        = string
  default     = null

  validation {
    condition     = var.custom_domain_hostname == null || can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.custom_domain_hostname))
    error_message = "custom_domain_hostname must be a lowercase host name such as writer.example.com."
  }
}

# ---------------------------------------------------------------------------
# Container app
# ---------------------------------------------------------------------------

variable "container_app_name" {
  description = "Name of the NEW v2 container app, created side by side with v1 in the existing environment. Its default FQDN is <name>.<environment default domain>."
  type        = string
  default     = "ca-blog-writer-v2"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,30}[a-z0-9]$", var.container_app_name)) && !strcontains(var.container_app_name, "--")
    error_message = "container_app_name must be 2-32 lowercase letters, digits or hyphens, start with a letter, end with a letter or digit and not contain \"--\"."
  }
}

variable "container_image" {
  description = "Image for the FIRST revision only. deploy owns the image afterwards (Terraform ignores image changes). The default Microsoft sample listens on 8080 but does not serve /api/health: apply with http_health_probes_enabled = false while it runs."
  type        = string
  default     = "mcr.microsoft.com/dotnet/samples:aspnetapp"
}

variable "container_image_repository" {
  description = "Repository of the shared registry that deploy pushes v2 images to (<acr_login_server>/<repository>:<git sha>). The ACR purge task only ever touches this repository."
  type        = string
  default     = "blog-writer-v2"

  validation {
    condition     = can(regex("^[a-z0-9]+(?:[._/-][a-z0-9]+)*$", var.container_image_repository))
    error_message = "container_image_repository must be a valid lowercase repository name."
  }
}

variable "target_port" {
  description = "Port the app listens on (ingress target and probe port)."
  type        = number
  default     = 8080
}

variable "http_health_probes_enabled" {
  description = "Use HTTP GET /api/health for the readiness and startup probes (the real image serves it; Easy Auth excludes it). Set false for the first applies while the placeholder image runs, which has no /api/health: TCP probes are used then. The liveness probe is always a TCP (process) check."
  type        = bool
  default     = true
}

variable "key_vault_secrets_set" {
  description = "Key Vault secrets the owner has ALREADY set (names from local.key_vault_secrets). Only these are wired into the container app as versionless Key Vault references: a reference to a missing secret makes the revision fail to provision. Keep [] for the first apply."
  type        = set(string)
  default     = []

  validation {
    # Keep in sync with local.key_vault_secrets.
    condition = alltrue([
      for name in var.key_vault_secrets_set : contains([
        "cookie-key", "easyauth-client-secret", "linkedin-client-secret",
        "portfolio-dispatch-token", "vapid-private-key", "vault-keys",
      ], name)
    ])
    error_message = "key_vault_secrets_set may only contain: cookie-key, easyauth-client-secret, linkedin-client-secret, portfolio-dispatch-token, vapid-private-key, vault-keys."
  }
}

variable "public_origin" {
  description = "BW_PUBLIC_ORIGIN (CSRF origin check). Null means https://<container_app_name>.<environment default domain>; set it to https://<custom domain> at cutover."
  type        = string
  default     = null

  validation {
    condition     = var.public_origin == null || can(regex("^https://[a-z0-9.-]+(:[0-9]{1,5})?$", var.public_origin))
    error_message = "public_origin must be an https origin without a path, e.g. https://writer.example.com."
  }
}

variable "linkedin_client_id" {
  description = "LinkedIn developer app client ID (BW_LINKEDIN_CLIENT_ID; not a secret). Null leaves it unset. Configure it together with Key Vault secret linkedin-client-secret."
  type        = string
  default     = null

  validation {
    condition     = var.linkedin_client_id == null || can(regex("^[A-Za-z0-9_-]{1,64}$", var.linkedin_client_id))
    error_message = "linkedin_client_id must be 1-64 letters, digits, '_' or '-'."
  }
}

variable "vault_primary_kid" {
  description = "BW_VAULT_PRIMARY: the key id new tokens are encrypted with. Only needed once the vault-keys ring holds more than one key (rotation). Null leaves it unset."
  type        = string
  default     = null

  validation {
    condition     = var.vault_primary_kid == null || can(regex("^[A-Za-z0-9_-]{1,32}$", var.vault_primary_kid))
    error_message = "vault_primary_kid must be 1-32 letters, digits, '_' or '-'."
  }
}

variable "portfolio_workflow" {
  description = "Workflow file the app dispatches in the portfolio repository (BW_PUBLISH_PORTFOLIO_WORKFLOW)."
  type        = string
  default     = "deploy.yml"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+\\.ya?ml$", var.portfolio_workflow))
    error_message = "portfolio_workflow must be a workflow file name such as deploy.yml."
  }
}

variable "publish_site_origin" {
  description = "Public origin of the portfolio site the publish step verifies (BW_PUBLISH_SITE_ORIGIN)."
  type        = string
  default     = "https://jatinmadan.com"

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+(:[0-9]{1,5})?$", var.publish_site_origin))
    error_message = "publish_site_origin must be an https origin without a path."
  }
}

# ---------------------------------------------------------------------------
# Foundry (Azure AI Services) and models
# ---------------------------------------------------------------------------

variable "foundry_project_enabled" {
  description = "Enable project management on the Foundry account and create one project, so the owner can use the Foundry portal (playground, Marketplace terms). Disabling later forces a new account."
  type        = bool
  default     = true
}

variable "model_capacity" {
  description = "GlobalStandard capacity (units of 1K tokens per minute) per deployment, merged over the defaults in locals.tf. Keys: gpt-6.2-sol, gpt-6.2-astra, gpt-6-luna, text-embedding-3-large, gpt-5.6-sol."
  type        = map(number)
  default     = {}

  validation {
    condition = alltrue([
      for k, v in var.model_capacity :
      contains(["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6-luna", "text-embedding-3-large", "gpt-5.6-sol"], k) && v >= 1
    ])
    error_message = "model_capacity keys must be known deployment names and values must be >= 1."
  }
}

variable "model_version_overrides" {
  description = "Model version per deployment, merged over the versions verified on 2026-11-25 (locals.tf). Bump before a version's retirement date."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for k in keys(var.model_version_overrides) :
      contains(["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6-luna", "text-embedding-3-large", "gpt-5.6-sol"], k)
    ])
    error_message = "model_version_overrides keys must be known deployment names."
  }
}

variable "enable_gpt_5_6_sol" {
  description = "Deploy gpt-5.6-sol as the research fallback (only if Phase 0 shows gpt-6.2 + web_search misbehaves). The app needs BW_AI_MODEL_OVERRIDES to use it."
  type        = bool
  default     = false
}

variable "claude_deployment_name" {
  description = "Name of the Claude deployment on the Foundry account (BW_AI_DEPLOYMENT_OPUS)."
  type        = string
  default     = "claude-opus-5-5"
}

variable "enable_claude_deployment" {
  description = "Manage the Claude Opus 5.5 deployment from Terraform (format 'Anthropic', Hosted on Azure). Never alongside a portal-created deployment of the same name on this account: import it instead."
  type        = bool
  default     = true
}

variable "claude_model_version" {
  description = "Claude model version: '2' = Hosted on Azure (default), '1' = Hosted on Anthropic."
  type        = string
  default     = "2"
}

variable "claude_capacity" {
  description = "GlobalStandard capacity of the Claude deployment (units; 5,000 were free in East US on 2026-11-26)."
  type        = number
  default     = 200

  validation {
    condition     = var.claude_capacity >= 1
    error_message = "claude_capacity must be >= 1."
  }
}

variable "claude_provider_industry" {
  description = "modelProviderData.industry for the Anthropic deployment (the lab's existing Claude deployments use \"technology\")."
  type        = string
  default     = "technology"
}

variable "claude_provider_organization" {
  description = "modelProviderData.organizationName for the Anthropic deployment. Kept in terraform.tfvars (no default) because it names the organization."
  type        = string
  default     = null
}

variable "claude_provider_country_code" {
  description = "modelProviderData.countryCode for the Anthropic deployment (ISO 3166-1 alpha-2)."
  type        = string
  default     = "US"

  validation {
    condition     = can(regex("^[A-Z]{2}$", var.claude_provider_country_code))
    error_message = "claude_provider_country_code must be a two-letter uppercase ISO country code."
  }
}

variable "enable_image_generation" {
  description = "Create a second Foundry account in image_location with a gpt-image-2 deployment (opt-in carousel illustrations). The app has no image settings yet, so nothing is passed to it."
  type        = bool
  default     = false
}

variable "image_model_version" {
  description = "gpt-image-2 version (verified in East US 2 on 2026-11-25)."
  type        = string
  default     = "2026-04-21"
}

variable "image_model_capacity" {
  description = "GlobalStandard capacity of the gpt-image-2 deployment."
  type        = number
  default     = 3
}

variable "enable_webiq" {
  description = "Create the Web IQ account (azapi, preview API, location global). Microsoft.WebIQ must be registered in the subscription (it is since 2026-11-26)."
  type        = bool
  default     = true
}

variable "webiq_sku" {
  description = "Web IQ account SKU."
  type        = string
  default     = "G3"
}

variable "webiq_endpoint" {
  description = "Web IQ data-plane endpoint for BW_RESEARCH_WEBIQ_ENDPOINT (https URL). The default is the Web IQ API host; the account's ARM 'endpoint' property is an APIM gateway the app does not call. Null falls back to that exported value."
  type        = string
  default     = "https://api.microsoft.ai"

  validation {
    condition     = var.webiq_endpoint == null || can(regex("^https://[^\\s]+$", var.webiq_endpoint))
    error_message = "webiq_endpoint must be an https URL."
  }
}

variable "webiq_scope" {
  description = "Entra scope of the managed-identity token the app requests for Web IQ (BW_RESEARCH_WEBIQ_SCOPE). Without it the app leaves Web IQ off."
  type        = string
  default     = "https://api.microsoft.ai/.default"
}

# ---------------------------------------------------------------------------
# Storage and registry
# ---------------------------------------------------------------------------

variable "storage_replication_type" {
  description = "Replication of the app storage account (backups live here, so geo-redundant by default)."
  type        = string
  default     = "GRS"

  validation {
    condition     = contains(["LRS", "ZRS", "GRS", "RAGRS", "GZRS", "RAGZRS"], var.storage_replication_type)
    error_message = "storage_replication_type must be one of LRS, ZRS, GRS, RAGRS, GZRS or RAGZRS."
  }
}

variable "acr_purge_enabled" {
  description = "Create the scheduled ACR task (in the shared registry) that purges old and untagged images of container_image_repository only."
  type        = bool
  default     = true
}

variable "acr_purge_schedule" {
  description = "Cron schedule (UTC) of the ACR purge task."
  type        = string
  default     = "17 3 * * *"
}

variable "acr_purge_keep" {
  description = "Number of most recent tags the purge task always keeps."
  type        = number
  default     = 10

  validation {
    condition     = var.acr_purge_keep >= 1 && floor(var.acr_purge_keep) == var.acr_purge_keep
    error_message = "acr_purge_keep must be a whole number >= 1."
  }
}

variable "acr_purge_older_than" {
  description = "Only tags older than this (acr purge --ago syntax, e.g. 30d) are purged."
  type        = string
  default     = "30d"

  validation {
    condition     = can(regex("^[0-9]+[dhm]$", var.acr_purge_older_than))
    error_message = "acr_purge_older_than must look like 30d, 12h or 90m."
  }
}

variable "render_chromium_sandbox" {
  description = "BW_RENDER_CHROMIUM_SANDBOX. Azure Container Apps does not grant the user namespaces Chromium's sandbox needs (verified 2026-11-27: launch fails), so it is off; the app then applies the compensating controls (non-dumpable process, minimal child environment, no network, --jitless; see docs/v2/build/render.md) and refuses to start rendering if they cannot be applied."
  type        = bool
  default     = false
}
