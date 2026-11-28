output "easyauth_client_id" {
  description = "Application (client) ID: set it as easyauth_client_id in infra/v2's terraform.tfvars (and the TF_VARS_FILE_PLATFORM secret)."
  value       = azuread_application.easyauth.client_id
}

output "owner_token_resource" {
  description = "Resource for owner bearer tokens (az account get-access-token --resource <this>)."
  value       = var.easyauth_cli_access_enabled ? "api://${azuread_application.easyauth.client_id}" : null
}

output "redirect_uris" {
  description = "Easy Auth callback URLs registered on the app."
  value       = local.easyauth_redirect_uris
}
