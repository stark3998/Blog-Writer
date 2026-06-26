# ── Entra ID (Azure AD) — Read existing app registration ─────────────

data "azuread_client_config" "current" {}

output "entra_client_id" {
  value       = var.entra_client_id
  description = "Entra ID Application (client) ID for Blog Writer"
}

output "entra_tenant_id" {
  value       = data.azuread_client_config.current.tenant_id
  description = "Entra ID Tenant ID"
}
