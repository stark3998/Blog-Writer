output "resource_group_name" {
  description = "State resource group."
  value       = azurerm_resource_group.state.name
}

output "storage_account_name" {
  description = "State storage account."
  value       = azurerm_storage_account.state.name
}

output "container_name" {
  description = "State container."
  value       = azurerm_storage_container.tfstate.name
}

output "state_key" {
  description = "Blob name for the main root's state."
  value       = var.state_key
}

# Write this to infra/v2/backend.hcl (gitignored):
#   terraform output -raw backend_config > ../backend.hcl
# The main root's backend block already sets use_azuread_auth = true; the
# values below are all the azurerm backend needs for Entra ID auth (no
# subscription or resource group lookup).
output "backend_config" {
  description = "Contents for infra/v2/backend.hcl (pass with terraform init -backend-config=backend.hcl)."
  value       = <<-EOT
    storage_account_name = "${azurerm_storage_account.state.name}"
    container_name       = "${azurerm_storage_container.tfstate.name}"
    key                  = "${var.state_key}"
  EOT
}
