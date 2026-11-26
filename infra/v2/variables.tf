# All real values live in terraform.tfvars (gitignored) or TF_VAR_* in CI.
# This repository is public: never commit subscription/tenant/object IDs,
# email addresses or people's names.

# ---------------------------------------------------------------------------
# Subscription, regions, naming
# ---------------------------------------------------------------------------

variable "subscription_id" {
  description = "Target subscription ID. Leave null to use ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "location" {
  description = "Primary region. The lab 'Allowed locations' policy only permits the regions in the validation list; the required models (GPT-6.2, Claude Opus 5.5) were verified as GlobalStandard in East US."
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

variable "resource_group_name" {
  description = "v2's own resource group. v1's resource group and ACR are shared with another app and are never referenced."
  type        = string
  default     = "rg-blog-writer-v2"
}

variable "name_prefix" {
  description = "Prefix for resource names: 3-14 lowercase letters, digits or hyphens, starting with a letter."
  type        = string
  default     = "blogwriter-v2"

  validation {
    # No "--": Key Vault and container app names reject consecutive hyphens.
    condition     = can(regex("^[a-z][a-z0-9-]{1,12}[a-z0-9]$", var.name_prefix)) && !strcontains(var.name_prefix, "--")
    error_message = "name_prefix must be 3-14 lowercase letters, digits or hyphens, start with a letter, not end with a hyphen and not contain \"--\"."
  }
}

variable "name_suffix" {
  description = "Suffix for globally unique names (Key Vault, storage, ACR, Cosmos DB, Foundry). Null derives a stable 6-character hash of the subscription and resource group. Override (4-6 lowercase letters/digits) if a soft-deleted name blocks a re-create."
  type        = string
  default     = null

  validation {
    condition     = var.name_suffix == null || can(regex("^[a-z0-9]{4,6}$", var.name_suffix))
    error_message = "name_suffix must be 4-6 lowercase letters or digits."
  }
}

# ---------------------------------------------------------------------------
# Lab-required resource group tags (deny policy). Applied, together with the
# app tags, to the resource group and every taggable resource.
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
  description = "Entra object ID of the owner: the only principal Easy Auth admits (allowedPrincipals) and OWNER_OID for the in-app fail-closed check. Also gets Key Vault Secrets Officer to set secret values."
  type        = string

  validation {
    # Rejects the all-zero placeholder from terraform.tfvars.example, which
    # would otherwise fail mid-apply (PrincipalNotFound on the role assignments).
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.owner_object_id)) && var.owner_object_id != "00000000-0000-0000-0000-000000000000"
    error_message = "owner_object_id must be the owner's real Entra object ID (a GUID, not the all-zero placeholder)."
  }
}

variable "alert_email" {
  description = "Email address for the Azure Monitor action group and the budget notifications."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "owner_data_access_enabled" {
  description = "Grant the owner data-plane access for migrations, restore drills and debugging: Cosmos DB Built-in Data Contributor on the database and Storage Blob Data Contributor on the storage account."
  type        = bool
  default     = true
}

variable "monthly_budget_amount" {
  description = "Advisory monthly budget for the resource group, in the billing currency. Budgets only notify; they never stop resources. The Claude Marketplace invoice is billed separately."
  type        = number
  default     = 300

  validation {
    condition     = var.monthly_budget_amount > 0
    error_message = "monthly_budget_amount must be positive."
  }
}

variable "log_retention_days" {
  description = "Log Analytics retention."
  type        = number
  default     = 30

  validation {
    condition     = var.log_retention_days >= 30 && var.log_retention_days <= 730
    error_message = "log_retention_days must be between 30 and 730."
  }
}

variable "enable_locks" {
  description = "CanNotDelete locks on Cosmos DB, storage, Foundry and Key Vault. Creating or removing locks needs Owner or User Access Administrator (not the ci_infra identity)."
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
  description = "Portfolio branch whose build may sign in (federated subject repo:<owner>/<portfolio>:ref:refs/heads/<branch>)."
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
  description = "Create the Entra app registration, its service principal (assignment required), the owner's assignment and the managed-identity federated credential with the azuread provider. Needs Graph rights (Application.ReadWrite.*); default off because the owner usually creates it in the portal."
  type        = bool
  default     = false
}

variable "entra_app_display_name" {
  description = "Display name of the Entra app registration when create_entra_app = true."
  type        = string
  default     = "Blog Writer v2"
}

variable "easyauth_client_id" {
  description = "Application (client) ID of the owner-created Entra app registration. Required unless create_entra_app = true."
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
  description = "Future custom domain (e.g. writer.example.com). Only adds an Entra redirect URI when create_entra_app = true and shows up in outputs; binding the domain and certificate is a later owner step."
  type        = string
  default     = null
}

# ---------------------------------------------------------------------------
# Container app
# ---------------------------------------------------------------------------

variable "container_image" {
  description = "Image for the FIRST revision only. deploy.yml owns the image afterwards (Terraform ignores image changes). The default Microsoft sample listens on 8080 but does not serve /api/health, so keep http_health_probes_enabled = false until a real image is deployed."
  type        = string
  default     = "mcr.microsoft.com/dotnet/samples:aspnetapp"
}

variable "container_image_repository" {
  description = "ACR repository deploy.yml pushes to (used by the ACR purge task filter and outputs)."
  type        = string
  default     = "blog-writer"

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
  description = "Use HTTP GET /api/health for the readiness and startup probes. Leave false until deploy.yml has rolled out an image that serves /api/health (TCP probes are used meanwhile). The liveness probe is always a TCP (process) check."
  type        = bool
  default     = false
}

variable "key_vault_secrets_set" {
  description = "Key Vault secrets the owner has ALREADY set (names from local.key_vault_secrets). Only these are wired into the container app as versionless Key Vault references: a reference to a missing secret makes the revision fail to provision."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for name in var.key_vault_secrets_set : contains([
        "app-encryption-key", "easyauth-client-secret", "linkedin-client-secret",
        "portfolio-dispatch-token", "vapid-private-key", "vapid-public-key",
      ], name)
    ])
    error_message = "key_vault_secrets_set may only contain: app-encryption-key, easyauth-client-secret, linkedin-client-secret, portfolio-dispatch-token, vapid-private-key, vapid-public-key."
  }
}

# ---------------------------------------------------------------------------
# Foundry (Azure AI Services) and models
# ---------------------------------------------------------------------------

variable "foundry_project_enabled" {
  description = "Enable project management on the Foundry account and create one project, so the owner can use the Foundry portal (Claude Marketplace deployment, playground). Disabling later forces a new account."
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
  description = "Deploy gpt-5.6-sol as the research fallback (only if Phase 0 shows gpt-6.2 + web_search misbehaves)."
  type        = bool
  default     = false
}

variable "claude_deployment_name" {
  description = "Name of the Claude deployment on the Foundry account (MODEL_DEPLOYMENT_CLAUDE). The owner creates it in the Foundry portal because the first Claude deployment must accept the Azure Marketplace offer."
  type        = string
  default     = "claude-opus-5-5"
}

variable "enable_claude_deployment" {
  description = "OPT-IN, UNVERIFIED: manage the Claude deployment from Terraform (format 'Anthropic'). Only after the owner has accepted the Marketplace offer in the portal, and never alongside a portal-created deployment of the same name (import it instead)."
  type        = bool
  default     = false
}

variable "claude_model_version" {
  description = "Claude model version for the opt-in deployment: '2' = Hosted on Azure (default), '1' = Hosted on Anthropic."
  type        = string
  default     = "2"
}

variable "claude_capacity" {
  description = "Capacity of the opt-in Claude deployment (1 unit = 1 RPM and 1K TPM)."
  type        = number
  default     = 40
}

variable "enable_image_generation" {
  description = "Create a second Foundry account in image_location with a gpt-image-2 deployment (opt-in carousel illustrations)."
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
  description = "Create the Web IQ account (azapi, preview API). Microsoft.WebIQ must be registered in the subscription first; it was NotRegistered on 2026-11-25."
  type        = bool
  default     = false
}

variable "webiq_sku" {
  description = "Web IQ account SKU."
  type        = string
  default     = "G3"
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
  description = "Create the scheduled ACR task that purges old and untagged images."
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
