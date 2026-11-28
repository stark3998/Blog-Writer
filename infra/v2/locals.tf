locals {
  tenant_id       = data.azurerm_client_config.current.tenant_id
  subscription_id = data.azurerm_client_config.current.subscription_id

  # ---------------------------------------------------------------------------
  # Tags: lab-required tags (exact names, including the space) plus app tags.
  # The resource group is shared and not managed here; every taggable resource
  # this root creates gets these.
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
    tf_root    = "infra/v2"
  }, local.required_tags)

  # ---------------------------------------------------------------------------
  # Names of the resources created here. The suffix is a stable hash (like
  # Bicep's uniqueString), so nothing subscription-specific is committed and
  # re-plans are deterministic.
  # ---------------------------------------------------------------------------
  suffix         = coalesce(var.name_suffix, substr(sha256("${local.subscription_id}/${var.resource_group_name}/v2-platform"), 0, 6))
  compact_prefix = replace(var.name_prefix, "-", "")

  names = {
    key_vault       = "kv-${var.name_prefix}-${local.suffix}"    # <= 24 chars
    storage_account = "st${local.compact_prefix}${local.suffix}" # <= 24 chars, no hyphens
    foundry         = "ais-${var.name_prefix}-${local.suffix}"   # also the custom subdomain
    foundry_images  = "ais-${var.name_prefix}-img-${local.suffix}"
    foundry_project = var.name_prefix
    webiq           = "webiq-${var.name_prefix}-${local.suffix}"
    action_group    = "ag-${var.name_prefix}"
    budget          = "budget-${var.name_prefix}"
    alert_prefix    = "alert-${var.name_prefix}"
    acr_purge_task  = "purge-${var.name_prefix}"
  }

  # User-assigned identities. easyauth is only used as a federated credential on
  # the Entra app registration (no Azure roles).
  identities = {
    app             = "id-${var.name_prefix}-app"
    easyauth        = "id-${var.name_prefix}-easyauth"
    portfolio_build = "id-${var.name_prefix}-portfolio-build"
    # The portfolio repository's deploy workflow (site_hosts): builds its image and
    # updates the portfolio container of the shared app.
    portfolio_deploy = "id-${var.name_prefix}-portfolio-deploy"
    ci_deploy        = "id-${var.name_prefix}-ci-deploy"
    ci_infra         = "id-${var.name_prefix}-ci-infra"
  }

  # ---------------------------------------------------------------------------
  # Existing resources (data sources in main.tf).
  # ---------------------------------------------------------------------------
  registry_id            = data.azapi_resource.registry.id
  acr_login_server       = data.azapi_resource.registry.output.login_server
  log_analytics_id       = data.azapi_resource.log_analytics.id
  log_analytics_location = data.azapi_resource.log_analytics.location
  cosmos_account_id      = data.azapi_resource.cosmos_account.id
  cosmos_endpoint        = data.azapi_resource.cosmos_account.output.endpoint

  # The only Cosmos container the portfolio build may read (published-blogs v2
  # contract: id == slug, partition key /id).
  portfolio_cosmos_container = "published-blogs"

  # Cosmos DB data-plane RBAC scopes use the dbs/colls form, not the ARM
  # sqlDatabases/containers IDs. Built from the data sources' names so the plan
  # fails early when the database or container is missing.
  cosmos_database_scope  = "${local.cosmos_account_id}/dbs/${data.azapi_resource.cosmos_database.name}"
  cosmos_portfolio_scope = "${local.cosmos_database_scope}/colls/${data.azapi_resource.cosmos_portfolio_container.name}"

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
  # Keep the names in sync with the validation of var.key_vault_secrets_set.
  # ---------------------------------------------------------------------------
  key_vault_secrets = {
    "cookie-key"               = { env = "BW_COOKIE_KEY", purpose = "HMAC key of the __Host-bw cookie (at least 32 bytes). Required: the app refuses to start in prod without it." }
    "vault-keys"               = { env = "BW_VAULT_KEYS", purpose = "Token vault key ring kid:base64key[,kid:base64key] (AES-256-GCM, 32-byte keys)" }
    "vapid-private-key"        = { env = "BW_NOTIFY_VAPID_PRIVATE_KEY", purpose = "Web Push VAPID private key (P-256). BW_NOTIFY_VAPID_SUBJECT is set only when this is wired." }
    "linkedin-client-secret"   = { env = "BW_LINKEDIN_CLIENT_SECRET", purpose = "LinkedIn developer app client secret" }
    "portfolio-dispatch-token" = { env = "BW_PUBLISH_PORTFOLIO_TOKEN", purpose = "Fine-grained GitHub PAT: Actions read/write on the portfolio repository only" }
    "easyauth-client-secret"   = { env = null, purpose = "Entra client secret, only when easyauth_credential = client_secret (read by Easy Auth, not by the app)" }
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
  # `az cognitiveservices model list --location eastus` (2026-11-25, re-checked
  # 2026-11-26). Deployment name == model name; the app reads the names from
  # BW_AI_DEPLOYMENT_*. gpt-5.6-sol has no BW_AI_DEPLOYMENT_* setting: route it
  # with BW_AI_MODEL_OVERRIDES / BW_AI_ROUTE_OVERRIDES if it is ever needed.
  # ---------------------------------------------------------------------------
  foundry_model_catalog = {
    "gpt-6.2-sol"            = { version = "2026-11-17", capacity = 500, env = "BW_AI_DEPLOYMENT_SOL", enabled = true }
    "gpt-6.2-astra"          = { version = "2026-11-17", capacity = 300, env = "BW_AI_DEPLOYMENT_ASTRA", enabled = true }
    "gpt-6-luna"             = { version = "2026-09-22", capacity = 200, env = "BW_AI_DEPLOYMENT_LUNA", enabled = true }
    "text-embedding-3-large" = { version = "1", capacity = 150, env = "BW_AI_DEPLOYMENT_EMBEDDING", enabled = true }
    "gpt-5.6-sol"            = { version = "2026-07-09", capacity = 100, env = null, enabled = var.enable_gpt_5_6_sol }
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
  foundry_openai_v1_base_url = "https://${local.foundry_subdomain}.openai.azure.com/openai/v1/"
  foundry_anthropic_base_url = "https://${local.foundry_subdomain}.services.ai.azure.com/anthropic/"

  image_openai_v1_base_url = var.enable_image_generation ? "https://${one(azurerm_cognitive_account.images[*].custom_subdomain_name)}.openai.azure.com/openai/v1/" : null

  # ---------------------------------------------------------------------------
  # Web IQ endpoint. The 2026-06-01-preview response shape is not documented:
  # var.webiq_endpoint wins, otherwise the first https URL found under a likely
  # property name, otherwise null (BW_RESEARCH_WEBIQ_ENDPOINT is then not set).
  # ---------------------------------------------------------------------------
  webiq_output = one(azapi_resource.webiq[*].output)
  webiq_exported_endpoint = try(
    tostring(local.webiq_output.properties.endpoint),
    tostring(local.webiq_output.properties.endpointUri),
    tostring(local.webiq_output.properties.endpointUrl),
    tostring(local.webiq_output.properties.endpoints.grounding),
    null,
  )
  webiq_endpoint_candidate = var.enable_webiq ? try(coalesce(var.webiq_endpoint, local.webiq_exported_endpoint), null) : null
  webiq_endpoint           = can(regex("^https://", local.webiq_endpoint_candidate)) ? local.webiq_endpoint_candidate : null

  # ---------------------------------------------------------------------------
  # App URL and Easy Auth. The default FQDN is <app>.<environment default
  # domain>; it is computed from the environment (not read from the app) so the
  # app's own settings and the Entra redirect URIs need no self-reference.
  # ---------------------------------------------------------------------------
  app_default_fqdn = "${var.container_app_name}.${data.azurerm_container_app_environment.this.default_domain}"
  public_origin    = coalesce(var.public_origin, "https://${local.app_default_fqdn}")

  entra_v2_issuer         = "https://login.microsoftonline.com/${local.tenant_id}/v2.0"
  easyauth_client_id      = var.easyauth_client_id
  easyauth_excluded_paths = ["/api/health"]
  # AllowAnonymous only with a public site; see easyauth.tf.
  easyauth_unauthenticated_action = length(var.site_hosts) > 0 ? "AllowAnonymous" : "RedirectToLoginPage"
  easyauth_fic_secret_name        = "override-use-mi-fic-assertion-client-id" # sentinel the auth sidecar recognises (lowercase for ACA secrets)
  easyauth_secret_setting         = var.easyauth_credential == "federated" ? local.easyauth_fic_secret_name : "easyauth-client-secret"

  # Programmatic owner access (easyauth_cli_access_enabled): the Azure CLI gets
  # a v2 access token for api://<client id> (aud = client ID); Easy Auth accepts
  # both forms. Assignment required + allowedPrincipals still admit only the owner.
  easyauth_identifier_uri    = local.easyauth_client_id == null ? null : "api://${local.easyauth_client_id}"
  easyauth_allowed_audiences = compact(var.easyauth_cli_access_enabled ? [local.easyauth_identifier_uri, local.easyauth_client_id] : [local.easyauth_client_id])
  # Deterministic scope ID: stable across plans, nothing tenant-specific committed.

  app_hostnames = compact([local.app_default_fqdn, var.custom_domain_hostname])

  # ---------------------------------------------------------------------------
  # Portfolio site (site_hosts): a second container in the same replica, bound
  # to 127.0.0.1 so only the app's gateway (and nothing else in the
  # environment) reaches it. The app proxies the site hosts to it and calls it
  # directly to revalidate pages after a publish.
  # ---------------------------------------------------------------------------
  site_enabled  = length(var.site_hosts) > 0
  site_upstream = "http://127.0.0.1:${var.portfolio_port}"
  site_origin   = local.site_enabled ? "https://${var.site_hosts[0]}" : var.publish_site_origin

  # ---------------------------------------------------------------------------
  # Storage
  # ---------------------------------------------------------------------------
  storage_containers = ["media", "published", "backups"]

  # The app writes daily dumps to backups/<YYYY-MM-DD>/<container>.jsonl.gz
  # (blogwriter.backups.dump). Every dated folder starts with "20", so this
  # prefix expires the dumps after 35 days while backups/v1-export/ (migration
  # input) is kept.
  backup_dump_prefix = "20"

  # ---------------------------------------------------------------------------
  # Container registry purge filter: anchored (\A...\z, no '$' for ACR task
  # aliases), dots escaped, so it can only ever match the v2 repository of the
  # shared registry.
  # ---------------------------------------------------------------------------
  acr_purge_filters = [
    for repository in compact([var.container_image_repository, local.site_enabled ? var.portfolio_image_repository : null]) :
    "\\A${replace(repository, ".", "\\.")}\\z:.*"
  ]

  # ---------------------------------------------------------------------------
  # Misc
  # ---------------------------------------------------------------------------
  portfolio_owner = coalesce(var.portfolio_github_owner, var.github_owner)
  github_issuer   = "https://token.actions.githubusercontent.com"
}
