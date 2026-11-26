data "azurerm_client_config" "current" {}

locals {
  tenant_id       = data.azurerm_client_config.current.tenant_id
  subscription_id = data.azurerm_client_config.current.subscription_id

  # ---------------------------------------------------------------------------
  # Tags: lab-required tags (exact names, including the space) plus app tags.
  # ---------------------------------------------------------------------------
  required_tags = {
    "Purpose"     = var.tag_purpose
    "Owner"       = var.tag_owner
    "Expiry Date" = var.tag_expiry_date
  }

  tags = merge(var.extra_tags, {
    app        = "blog-writer"
    generation = "v2"
    managed_by = "terraform"
  }, local.required_tags)

  # ---------------------------------------------------------------------------
  # Names. The suffix is a stable hash (like Bicep's uniqueString), so nothing
  # subscription-specific is committed and re-plans are deterministic.
  # ---------------------------------------------------------------------------
  suffix         = coalesce(var.name_suffix, substr(sha256("${local.subscription_id}/${var.resource_group_name}"), 0, 6))
  compact_prefix = replace(var.name_prefix, "-", "")

  names = {
    log_analytics   = "log-${var.name_prefix}"
    environment     = "cae-${var.name_prefix}"
    container_app   = "ca-${var.name_prefix}"
    key_vault       = "kv-${var.name_prefix}-${local.suffix}"    # <= 24 chars
    storage_account = "st${local.compact_prefix}${local.suffix}" # <= 24 chars, no hyphens
    cosmos_account  = "cosmos-${var.name_prefix}-${local.suffix}"
    registry        = "cr${local.compact_prefix}${local.suffix}"
    foundry         = "ais-${var.name_prefix}-${local.suffix}" # also the custom subdomain
    foundry_images  = "ais-${var.name_prefix}-img-${local.suffix}"
    foundry_project = var.name_prefix
    webiq           = "webiq-${var.name_prefix}-${local.suffix}"
    action_group    = "ag-${var.name_prefix}"
    budget          = "budget-${var.name_prefix}"
  }

  # User-assigned identities. easyauth is only used as a federated credential on
  # the Entra app registration (no Azure roles).
  identities = {
    app             = "id-${var.name_prefix}-app"
    easyauth        = "id-${var.name_prefix}-easyauth"
    portfolio_build = "id-${var.name_prefix}-portfolio-build"
    ci_deploy       = "id-${var.name_prefix}-ci-deploy"
    ci_infra        = "id-${var.name_prefix}-ci-infra"
  }

  # ---------------------------------------------------------------------------
  # Built-in Azure RBAC role definition IDs (global constants, verified with
  # `az role definition list` on 2026-11-25). IDs instead of names, so a
  # rename (e.g. "Azure AI User" -> "Foundry User") cannot break a plan.
  # ---------------------------------------------------------------------------
  role_ids = {
    acr_pull                      = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
    acr_push                      = "8311e382-0749-4cb8-b61a-304f252e45ec"
    acr_tasks_contributor         = "fb382eab-e894-4461-af04-94435c366c3f" # Container Registry Tasks Contributor (az acr build)
    container_apps_contributor    = "358470bc-b998-42bd-ab17-a7e34c199c0f"
    contributor                   = "b24988ac-6180-42a0-ab88-20f7382dd24c"
    foundry_user                  = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
    key_vault_secrets_officer     = "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
    key_vault_secrets_user        = "4633458b-17de-408a-b874-0445c86b69e6"
    rbac_administrator            = "f58310d9-a9f6-439a-9e8d-f62e7b41a168" # Role Based Access Control Administrator
    storage_blob_data_contributor = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
    storage_blob_data_reader      = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
  }

  role_definition_ids = {
    for name, id in local.role_ids :
    name => "/subscriptions/${local.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${id}"
  }

  # Cosmos DB built-in data-plane roles.
  cosmos_data_reader_role      = "00000000-0000-0000-0000-000000000001"
  cosmos_data_contributor_role = "00000000-0000-0000-0000-000000000002"

  # ---------------------------------------------------------------------------
  # Key Vault: Terraform creates the vault, never the secrets. The owner sets
  # each value once (az keyvault secret set); the container app reads them
  # through versionless Key Vault references, so values never enter Terraform,
  # CI or state. env = null means "not exposed as an environment variable".
  # ---------------------------------------------------------------------------
  key_vault_secrets = {
    "app-encryption-key"       = { env = "APP_ENCRYPTION_KEY", purpose = "AES-GCM key for stored tokens (v1.<kid>.<nonce>.<ct>) and the __Host-bw cookie HMAC derivation" }
    "easyauth-client-secret"   = { env = null, purpose = "Entra client secret, only when easyauth_credential = client_secret" }
    "linkedin-client-secret"   = { env = "LINKEDIN_CLIENT_SECRET", purpose = "LinkedIn developer app client secret" }
    "portfolio-dispatch-token" = { env = "PORTFOLIO_DISPATCH_TOKEN", purpose = "Fine-grained GitHub PAT, actions:write on the portfolio repo only" }
    "vapid-private-key"        = { env = "VAPID_PRIVATE_KEY", purpose = "Web Push VAPID private key" }
    "vapid-public-key"         = { env = "VAPID_PUBLIC_KEY", purpose = "Web Push VAPID public key (kept next to its private key)" }
  }

  # Secrets wired into the app (versionless references, read by the app identity).
  app_key_vault_secret_names = sort(tolist(var.key_vault_secrets_set))

  app_secret_env = {
    for name in local.app_key_vault_secret_names :
    local.key_vault_secrets[name].env => name
    if local.key_vault_secrets[name].env != null
  }

  # ---------------------------------------------------------------------------
  # Foundry model deployments. Versions verified with
  # `az cognitiveservices model list --location eastus` on 2026-11-25.
  # Deployment name == model name; the app reads names from MODEL_DEPLOYMENT_*.
  # ---------------------------------------------------------------------------
  foundry_model_catalog = {
    "gpt-6.2-sol"            = { version = "2026-11-17", capacity = 500, env = "MODEL_DEPLOYMENT_SOL", enabled = true }
    "gpt-6.2-astra"          = { version = "2026-11-17", capacity = 300, env = "MODEL_DEPLOYMENT_ASTRA", enabled = true }
    "gpt-6-luna"             = { version = "2026-09-22", capacity = 200, env = "MODEL_DEPLOYMENT_LUNA", enabled = true }
    "text-embedding-3-large" = { version = "1", capacity = 150, env = "MODEL_DEPLOYMENT_EMBEDDING", enabled = true }
    "gpt-5.6-sol"            = { version = "2026-07-09", capacity = 100, env = "MODEL_DEPLOYMENT_SOL_FALLBACK", enabled = var.enable_gpt_5_6_sol }
  }

  foundry_deployments = {
    for name, model in local.foundry_model_catalog :
    name => {
      version  = lookup(var.model_version_overrides, name, model.version)
      capacity = lookup(var.model_capacity, name, model.capacity)
      env      = model.env
    }
    if model.enabled
  }

  # Data-plane endpoints of the Foundry account (custom subdomain == name).
  foundry_subdomain          = azurerm_cognitive_account.foundry.custom_subdomain_name
  foundry_openai_endpoint    = "https://${local.foundry_subdomain}.openai.azure.com/"
  foundry_openai_v1_base_url = "https://${local.foundry_subdomain}.openai.azure.com/openai/v1/"
  foundry_anthropic_base_url = "https://${local.foundry_subdomain}.services.ai.azure.com/anthropic/"

  image_openai_v1_base_url = var.enable_image_generation ? "https://${one(azurerm_cognitive_account.images[*].custom_subdomain_name)}.openai.azure.com/openai/v1/" : null

  # ---------------------------------------------------------------------------
  # Easy Auth
  # ---------------------------------------------------------------------------
  entra_v2_issuer          = "https://login.microsoftonline.com/${local.tenant_id}/v2.0"
  easyauth_client_id       = var.create_entra_app ? one(azuread_application.easyauth[*].client_id) : var.easyauth_client_id
  easyauth_excluded_paths  = ["/api/health"]
  easyauth_fic_secret_name = "override-use-mi-fic-assertion-client-id" # sentinel the auth sidecar recognises (lowercase for ACA secrets)
  easyauth_secret_setting  = var.easyauth_credential == "federated" ? local.easyauth_fic_secret_name : "easyauth-client-secret"

  app_hostnames          = compact([azurerm_container_app.app.ingress[0].fqdn, var.custom_domain_hostname])
  easyauth_redirect_uris = [for host in local.app_hostnames : "https://${host}/.auth/login/aad/callback"]

  # ---------------------------------------------------------------------------
  # Storage
  # ---------------------------------------------------------------------------
  storage_containers = ["media", "published", "backups"]

  # Daily JSONL dumps go to backups/<backup_dump_prefix>...; only that prefix
  # expires after 35 days, so backups/v1-export/ (migration input) is kept.
  backup_dump_prefix = "daily/"

  # ---------------------------------------------------------------------------
  # Misc
  # ---------------------------------------------------------------------------
  portfolio_owner = coalesce(var.portfolio_github_owner, var.github_owner)
  github_issuer   = "https://token.actions.githubusercontent.com"
}
