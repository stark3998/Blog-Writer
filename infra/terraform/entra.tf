# ── Entra ID (Azure AD) App Registration ────────────────────────────

data "azuread_client_config" "current" {}

resource "azuread_application" "blog_writer" {
  display_name = "${var.project_name}-${var.environment}"
  owners       = [data.azuread_client_config.current.object_id]

  sign_in_audience = "AzureADMyOrg"

  single_page_application {
    redirect_uris = var.entra_spa_redirect_uris
  }

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      admin_consent_description  = "Allow the application to access Blog Writer on behalf of the signed-in user."
      admin_consent_display_name = "Access Blog Writer"
      enabled                    = true
      id                         = random_uuid.scope_id.result
      type                       = "User"
      user_consent_description   = "Allow the application to access Blog Writer on your behalf."
      user_consent_display_name  = "Access Blog Writer"
      value                      = "access_as_user"
    }
  }

  web {
    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = true
    }
  }
}

resource "random_uuid" "scope_id" {}

resource "azuread_application_password" "blog_writer" {
  application_id = azuread_application.blog_writer.id
  display_name   = "blog-writer-backend-${var.environment}"
  end_date       = "2027-01-01T00:00:00Z"
}

resource "azuread_service_principal" "blog_writer" {
  client_id = azuread_application.blog_writer.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

output "entra_client_id" {
  value       = azuread_application.blog_writer.client_id
  description = "Entra ID Application (client) ID for Blog Writer"
}

output "entra_tenant_id" {
  value       = data.azuread_client_config.current.tenant_id
  description = "Entra ID Tenant ID"
}
