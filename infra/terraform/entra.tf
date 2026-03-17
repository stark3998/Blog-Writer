# ── Entra ID (Azure AD) — Read existing app registration ─────────────

data "azuread_client_config" "current" {}

data "azuread_application" "blog_writer" {
  client_id = var.entra_client_id
}

data "azuread_service_principal" "blog_writer" {
  client_id = var.entra_client_id
}

output "entra_client_id" {
  value       = data.azuread_application.blog_writer.client_id
  description = "Entra ID Application (client) ID for Blog Writer"
}

output "entra_tenant_id" {
  value       = data.azuread_client_config.current.tenant_id
  description = "Entra ID Tenant ID"
}
