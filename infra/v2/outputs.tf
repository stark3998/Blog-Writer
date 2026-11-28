# Non-sensitive outputs only (endpoints, names, identity client/principal IDs).
# CI logs of this public repository can show them: store the IDs you copy into
# GitHub as environment *secrets* so GitHub masks them in logs.

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

output "resource_group_name" {
  description = "Existing (shared) resource group the v2 resources live in."
  value       = data.azurerm_resource_group.this.name
}

output "container_app_name" {
  description = "v2 container app (deploy: az containerapp update --name)."
  value       = azurerm_container_app.app.name
}

output "container_app_environment_name" {
  description = "Existing Container Apps environment the v2 app runs in (shared with v1)."
  value       = data.azurerm_container_app_environment.this.name
}

output "container_app_fqdn" {
  description = "Default FQDN of the v2 container app."
  value       = azurerm_container_app.app.ingress[0].fqdn
}

output "container_app_url" {
  description = "App URL (Easy Auth redirects unauthenticated browsers to Entra ID)."
  value       = "https://${azurerm_container_app.app.ingress[0].fqdn}"
}

output "public_origin" {
  description = "BW_PUBLIC_ORIGIN the app is configured with."
  value       = local.public_origin
}

output "app_env_names" {
  description = "Names of the environment variables Terraform sets on the app (values not shown): plain values and Key Vault references. BW_BUILD_SHA and BW_SPA_DIR come from the image."
  value = {
    plain     = sort(keys(local.app_env))
    key_vault = sort(keys(local.app_secret_env))
  }
}

# ---------------------------------------------------------------------------
# Easy Auth / Entra
# ---------------------------------------------------------------------------

output "easyauth_client_id" {
  description = "Application (client) ID of the Entra app registration Easy Auth uses."
  value       = local.easyauth_client_id
}

output "easyauth_token_resource" {
  description = "Resource for owner bearer tokens: az account get-access-token --resource <this> (null when easyauth_cli_access_enabled = false)."
  value       = var.easyauth_cli_access_enabled ? local.easyauth_identifier_uri : null
}

output "easyauth_federated_credential" {
  description = "Federated credential the Entra app registration must carry (managed by the owner in infra/v2/entra)."
  value = {
    issuer    = local.entra_v2_issuer
    subject   = azurerm_user_assigned_identity.this["easyauth"].principal_id
    audiences = ["api://AzureADTokenExchange"]
  }
}

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

output "acr_name" {
  description = "Shared registry name (az acr build --registry)."
  value       = var.container_registry_name
}

output "acr_login_server" {
  description = "Shared registry login server."
  value       = local.acr_login_server
}

output "container_image_repository" {
  description = "Repository deploy pushes to: <acr_login_server>/<repository>:<git sha>."
  value       = var.container_image_repository
}

# ---------------------------------------------------------------------------
# Data (Cosmos DB from infra/v2/data, storage, Key Vault)
# ---------------------------------------------------------------------------

output "cosmos_endpoint" {
  description = "v2 Cosmos DB endpoint (BW_COSMOS_ENDPOINT), from infra/v2/data."
  value       = local.cosmos_endpoint
}

output "cosmos_account_name" {
  description = "v2 Cosmos DB account (owned by infra/v2/data)."
  value       = var.cosmos_account_name
}

output "cosmos_database_name" {
  description = "v2 Cosmos DB database (BW_COSMOS_DATABASE)."
  value       = var.cosmos_database_name
}

output "storage_account_name" {
  description = "v2 storage account."
  value       = azurerm_storage_account.this.name
}

output "storage_blob_endpoint" {
  description = "Blob endpoint of the v2 storage account (BW_STORAGE_BLOB_ENDPOINT)."
  value       = azurerm_storage_account.this.primary_blob_endpoint
}

output "key_vault_name" {
  description = "Key Vault name (the owner sets secret values here)."
  value       = azurerm_key_vault.this.name
}

output "key_vault_uri" {
  description = "Key Vault URI."
  value       = azurerm_key_vault.this.vault_uri
}

output "key_vault_expected_secrets" {
  description = "Secret name => environment variable and purpose (values set by the owner, never by Terraform)."
  value = {
    for name, s in local.key_vault_secrets :
    name => { env = coalesce(s.env, "(Easy Auth only)"), purpose = s.purpose }
  }
}

output "key_vault_secrets_not_wired" {
  description = "Expected secrets not yet listed in key_vault_secrets_set (not referenced by the container app)."
  value       = sort(tolist(setsubtract(keys(local.key_vault_secrets), var.key_vault_secrets_set)))
}

# ---------------------------------------------------------------------------
# Foundry and Web IQ
# ---------------------------------------------------------------------------

output "foundry_account_name" {
  description = "Foundry (AI Services) account; its custom subdomain is BW_AI_ANTHROPIC_RESOURCE."
  value       = azurerm_cognitive_account.foundry.name
}

output "foundry_endpoint" {
  description = "Foundry account endpoint."
  value       = azurerm_cognitive_account.foundry.endpoint
}

output "foundry_openai_v1_base_url" {
  description = "Azure OpenAI GA v1 base URL (BW_AI_OPENAI_BASE_URL, Responses API)."
  value       = local.foundry_openai_v1_base_url
}

output "foundry_anthropic_base_url" {
  description = "Claude base URL on this Foundry resource (derived by the app from BW_AI_ANTHROPIC_RESOURCE)."
  value       = local.foundry_anthropic_base_url
}

output "model_deployments" {
  description = "Deployment name => version and capacity."
  value = merge(
    { for name, d in local.foundry_deployments : name => { version = d.version, capacity = d.capacity } },
    { (var.claude_deployment_name) = var.enable_claude_deployment ? { version = var.claude_model_version, capacity = var.claude_capacity } : { version = "not managed (enable_claude_deployment = false)", capacity = null } },
  )
}

output "image_openai_v1_base_url" {
  description = "v1 base URL of the optional gpt-image-2 account (null when disabled)."
  value       = local.image_openai_v1_base_url
}

output "webiq_account_name" {
  description = "Web IQ account name (null when enable_webiq = false)."
  value       = one(azapi_resource.webiq[*].name)
}

output "webiq_endpoint" {
  description = "BW_RESEARCH_WEBIQ_ENDPOINT given to the app (null: unknown; set var.webiq_endpoint, see README)."
  value       = local.webiq_endpoint
}

output "webiq_properties" {
  description = "Properties the Web IQ API returned (to find the endpoint property name)."
  value       = try(local.webiq_output.properties, null)
}

# ---------------------------------------------------------------------------
# Identities, CI, monitoring
# ---------------------------------------------------------------------------

output "identity_client_ids" {
  description = "Client IDs of the user-assigned identities (AZURE_CLIENT_ID for each GitHub workflow; the app's is also the Web IQ binding)."
  value       = { for key, identity in azurerm_user_assigned_identity.this : key => identity.client_id }
}

output "identity_principal_ids" {
  description = "Principal (object) IDs of the user-assigned identities."
  value       = { for key, identity in azurerm_user_assigned_identity.this : key => identity.principal_id }
}

output "github_oidc_subjects" {
  description = "Federated credential subject per identity."
  value       = { for key, f in local.github_federations : key => f.subject }
}

output "log_analytics_workspace_name" {
  description = "Existing Log Analytics workspace (ContainerAppConsoleLogs_CL / ContainerAppSystemLogs_CL)."
  value       = var.log_analytics_workspace_name
}

output "alert_rules" {
  description = "Log alert rule name => enabled."
  value       = { for key, rule in azurerm_monitor_scheduled_query_rules_alert_v2.app : rule.name => rule.enabled }
}

output "site_hosts" {
  description = "Public host names of the portfolio site this app serves (canonical first); scripts/bind-site-domains.sh binds them."
  value       = var.site_hosts
}

output "site_origin" {
  description = "Public origin of the portfolio site (BW_PUBLISH_SITE_ORIGIN)."
  value       = local.site_origin
}
