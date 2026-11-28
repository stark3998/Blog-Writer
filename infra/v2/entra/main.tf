data "azurerm_client_config" "current" {}

data "azurerm_container_app_environment" "this" {
  name                = var.environment_name
  resource_group_name = var.resource_group_name
}

data "azurerm_user_assigned_identity" "easyauth" {
  name                = var.easyauth_identity_name
  resource_group_name = var.resource_group_name
}

locals {
  app_default_fqdn       = "${var.container_app_name}.${data.azurerm_container_app_environment.this.default_domain}"
  app_hostnames          = compact([local.app_default_fqdn, var.custom_domain_hostname])
  easyauth_redirect_uris = [for host in local.app_hostnames : "https://${host}/.auth/login/aad/callback"]
  entra_v2_issuer        = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
  easyauth_scope_id      = uuidv5("url", "urn:blog-writer:v2:easyauth:user_impersonation")
  azure_cli_client_id    = "04b07795-8ddb-461a-bbee-02f9e1bf7b46" # Microsoft's public Azure CLI client
}

# ---------------------------------------------------------------------------
# App registration and service principal (assignment required, owner only)
# ---------------------------------------------------------------------------

resource "azuread_application" "easyauth" {
  display_name     = var.entra_app_display_name
  sign_in_audience = "AzureADMyOrg"
  # The owner, not "whoever runs Terraform", so a run under another identity does not try to
  # rewrite ownership.
  owners = [var.owner_object_id]

  api {
    # v2 access tokens: issuer .../v2.0 (matches openIdIssuer) and aud = the client ID.
    requested_access_token_version = 2

    # Always declared, enabled only with easyauth_cli_access_enabled: Entra refuses to delete
    # an enabled scope, so turning CLI access off just disables it.
    oauth2_permission_scope {
      id                         = local.easyauth_scope_id
      value                      = "user_impersonation"
      type                       = "User"
      enabled                    = var.easyauth_cli_access_enabled
      admin_consent_display_name = "Access Blog Writer v2"
      admin_consent_description  = "Lets the signed-in owner call the Blog Writer v2 app with a bearer token (live testing from the Azure CLI)."
      user_consent_display_name  = "Access Blog Writer v2"
      user_consent_description   = "Lets you call the Blog Writer v2 app with a bearer token."
    }
  }

  web {
    redirect_uris = local.easyauth_redirect_uris

    # Easy Auth's login flow requests response_type=code id_token.
    implicit_grant {
      id_token_issuance_enabled = true
    }
  }

  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read (delegated)
      type = "Scope"
    }
  }

  lifecycle {
    # azuread_application_identifier_uri owns identifier_uris: api://<client id> cannot be set
    # here without the application referencing itself.
    ignore_changes = [identifier_uris]
  }
}

resource "azuread_service_principal" "easyauth" {
  client_id = azuread_application.easyauth.client_id
  owners    = [var.owner_object_id]

  # "Assignment required": only assigned principals can sign in or get a token at all.
  app_role_assignment_required = true
}

resource "azuread_app_role_assignment" "owner" {
  app_role_id         = "00000000-0000-0000-0000-000000000000" # default access
  principal_object_id = var.owner_object_id
  resource_object_id  = azuread_service_principal.easyauth.object_id
}

# The easyauth managed identity as a federated credential of the app registration: no client
# secret exists anywhere.
resource "azuread_application_federated_identity_credential" "easyauth" {
  count = var.federated_credential_enabled ? 1 : 0

  application_id = azuread_application.easyauth.id
  display_name   = "easyauth-managed-identity"
  description    = "Container Apps Easy Auth sidecar (user-assigned managed identity)"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = local.entra_v2_issuer
  subject        = data.azurerm_user_assigned_identity.easyauth.principal_id
}

# ---------------------------------------------------------------------------
# Programmatic owner access for live testing (easyauth_cli_access_enabled):
#   az account get-access-token --resource api://<client id> --query accessToken -o tsv
# ---------------------------------------------------------------------------

resource "azuread_application_identifier_uri" "easyauth" {
  count = var.easyauth_cli_access_enabled ? 1 : 0

  application_id = azuread_application.easyauth.id
  identifier_uri = "api://${azuread_application.easyauth.client_id}"
}

# The Azure CLI may request user_impersonation without a consent prompt.
resource "azuread_application_pre_authorized" "azure_cli" {
  count = var.easyauth_cli_access_enabled ? 1 : 0

  application_id       = azuread_application.easyauth.id
  authorized_client_id = local.azure_cli_client_id
  permission_ids       = [local.easyauth_scope_id]
}
