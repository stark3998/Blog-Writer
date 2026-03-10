output "resource_group_name" {
  description = "Azure resource group name."
  value       = azurerm_resource_group.this.name
}

output "container_app_url" {
  description = "Public URL of the deployed Container App."
  value       = "https://${azurerm_container_app.this.latest_revision_fqdn}"
}

output "container_app_fqdn" {
  description = "FQDN of the deployed Container App."
  value       = azurerm_container_app.this.latest_revision_fqdn
}

output "acr_name" {
  description = "Azure Container Registry name."
  value       = azurerm_container_registry.this.name
}

output "acr_login_server" {
  description = "Azure Container Registry login server."
  value       = azurerm_container_registry.this.login_server
}

output "portal_resource_group_url" {
  description = "Azure Portal URL for the deployed resource group."
  value       = "https://portal.azure.com/#@/resource/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${azurerm_resource_group.this.name}/overview"
}
