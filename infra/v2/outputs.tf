# Non-sensitive outputs only (endpoints, names, identity client/principal IDs).
# CI logs of this public repository can show them: store the IDs you copy into
# GitHub as environment *secrets* so GitHub masks them in logs.

output "resource_group_name" {
  description = "v2 resource group."
  value       = azurerm_resource_group.this.name
}

output "container_app_name" {
  description = "Container app name (deploy.yml: az containerapp update --name)."
  value       = azurerm_container_app.app.name
}

output "container_app_fqdn" {
  description = "Default FQDN of the container app."
  value       = azurerm_container_app.app.ingress[0].fqdn
}

output "container_app_url" {
  description = "App URL (Easy Auth redirects unauthenticated requests to Entra ID)."
  value       = "https://${azurerm_container_app.app.ingress[0].fqdn}"
}

output "easyauth_redirect_uris" {
  description = "Web redirect URIs to register on the Entra app registration."
  value       = local.easyauth_redirect_uris
}

output "easyauth_federated_credential" {
  description = "Federated credential to add to the Entra app registration (owner step, unless create_entra_app = true)."
  value = {
    issuer    = local.entra_v2_issuer
    subject   = azurerm_user_assigned_identity.this["easyauth"].principal_id
    audiences = ["api://AzureADTokenExchange"]
  }
}

output "acr_name" {
  description = "Registry name (deploy.yml: az acr build --registry)."
  value       = azurerm_container_registry.this.name
}

output "acr_login_server" {
  description = "Registry login server."
  value       = azurerm_container_registry.this.login_server
}

output "container_image_repository" {
  description = "Repository deploy.yml pushes to: <acr_login_server>/<repository>:<git sha>."
  value       = var.container_image_repository
}

output "cosmos_endpoint" {
  description = "Cosmos DB endpoint."
  value       = azurerm_cosmosdb_account.this.endpoint
}

output "cosmos_account_name" {
  description = "Cosmos DB account."
  value       = azurerm_cosmosdb_account.this.name
}

output "cosmos_database_name" {
  description = "Cosmos DB database."
  value       = azurerm_cosmosdb_sql_database.this.name
}

output "cosmos_containers" {
  description = "Container name => partition key."
  value       = { for name, c in local.cosmos_containers : name => c.partition_key }
}

output "storage_account_name" {
  description = "App storage account."
  value       = azurerm_storage_account.this.name
}

output "storage_blob_endpoint" {
  description = "Blob endpoint of the app storage account."
  value       = azurerm_storage_account.this.primary_blob_endpoint
}

output "key_vault_name" {
  description = "Key Vault name (owner sets secret values here)."
  value       = azurerm_key_vault.this.name
}

output "key_vault_uri" {
  description = "Key Vault URI."
  value       = azurerm_key_vault.this.vault_uri
}

output "key_vault_expected_secrets" {
  description = "Secret names the app expects (values set by the owner, never by Terraform)."
  value       = { for name, s in local.key_vault_secrets : name => s.purpose }
}

output "key_vault_secrets_not_wired" {
  description = "Expected secrets not yet listed in key_vault_secrets_set (not referenced by the container app)."
  value       = sort(tolist(setsubtract(keys(local.key_vault_secrets), var.key_vault_secrets_set)))
}

output "foundry_account_name" {
  description = "Foundry (AI Services) account; also the resource name for Claude (ANTHROPIC_FOUNDRY_RESOURCE)."
  value       = azurerm_cognitive_account.foundry.name
}

output "foundry_endpoint" {
  description = "Foundry account endpoint."
  value       = azurerm_cognitive_account.foundry.endpoint
}

output "foundry_openai_v1_base_url" {
  description = "Azure OpenAI GA v1 base URL (Responses API)."
  value       = local.foundry_openai_v1_base_url
}

output "foundry_anthropic_base_url" {
  description = "Claude base URL on this Foundry resource."
  value       = local.foundry_anthropic_base_url
}

output "model_deployments" {
  description = "Deployment name => version and capacity (Claude is owner-provisioned unless enable_claude_deployment)."
  value = merge(
    { for name, d in local.foundry_deployments : name => { version = d.version, capacity = d.capacity } },
    { (var.claude_deployment_name) = var.enable_claude_deployment ? { version = var.claude_model_version, capacity = var.claude_capacity } : { version = "owner-provisioned", capacity = null } },
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

output "identity_client_ids" {
  description = "Client IDs of the user-assigned identities (AZURE_CLIENT_ID for each GitHub workflow; the app's is also the Web IQ binding)."
  value       = { for key, identity in azurerm_user_assigned_identity.this : key => identity.client_id }
}

output "identity_principal_ids" {
  description = "Principal (object) IDs of the user-assigned identities (ci_infra's goes into the bootstrap state_contributor_principal_ids)."
  value       = { for key, identity in azurerm_user_assigned_identity.this : key => identity.principal_id }
}

output "github_oidc_subjects" {
  description = "Federated credential subject per identity."
  value       = { for key, f in local.github_federations : key => f.subject }
}

output "log_analytics_workspace_name" {
  description = "Log Analytics workspace (ContainerAppConsoleLogs / ContainerAppSystemLogs tables)."
  value       = azurerm_log_analytics_workspace.this.name
}
