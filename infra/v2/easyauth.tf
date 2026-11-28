# Easy Auth (Microsoft Entra ID) in front of the v2 container app.
#
# azapi because azurerm has no resource for Microsoft.App/containerApps/authConfigs.
# The body below validates against azapi's embedded schema for 2026-07-01.
# Container Apps has no 'requireAuthentication' property (App Service does):
# "require authentication" is unauthenticatedClientAction != AllowAnonymous.

resource "azapi_resource" "easy_auth" {
  type      = "Microsoft.App/containerApps/authConfigs@2026-07-01"
  name      = "current"
  parent_id = azurerm_container_app.app.id

  body = {
    properties = {
      platform = {
        enabled = true
      }

      globalValidation = {
        unauthenticatedClientAction = "RedirectToLoginPage"
        redirectToProvider          = "azureactivedirectory"
        # Exactly one anonymous path; it returns only status and SHA.
        excludedPaths = local.easyauth_excluded_paths
      }

      identityProviders = {
        azureActiveDirectory = {
          enabled = true

          registration = {
            clientId     = local.easyauth_client_id
            openIdIssuer = local.entra_v2_issuer
            # 'federated': name of the ACA secret holding the easyauth identity's
            # client ID (sidecar sentinel). 'client_secret': name of the ACA
            # secret that references Key Vault secret easyauth-client-secret.
            clientSecretSettingName = local.easyauth_secret_setting
          }

          validation = {
            # The client ID (browser sign-in) and, with easyauth_cli_access_enabled,
            # api://<client id> for bearer tokens the Azure CLI gets.
            allowedAudiences = local.easyauth_allowed_audiences

            defaultAuthorizationPolicy = {
              allowedPrincipals = {
                identities = [var.owner_object_id]
              }
            }
          }
        }
      }

      login = {
        # No token store: tokens are never persisted by the platform.
        tokenStore = {
          enabled = false
        }

        cookieExpiration = {
          convention       = "FixedTime"
          timeToExpiration = format("%d.00:00:00", var.easyauth_cookie_expiration_days)
        }
      }

      httpSettings = {
        requireHttps = true
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.create_entra_app || var.easyauth_client_id != null
      error_message = "Set easyauth_client_id to the owner-created Entra app registration's client ID (or keep create_entra_app = true). Without it the app would be exposed without authentication."
    }

    precondition {
      condition     = var.easyauth_credential == "federated" || contains(local.app_key_vault_secret_names, "easyauth-client-secret")
      error_message = "easyauth_credential = \"client_secret\" needs Key Vault secret 'easyauth-client-secret' set by the owner and listed in key_vault_secrets_set."
    }
  }

  # With create_entra_app = true, finish the Entra side first (no-op otherwise).
  depends_on = [
    azuread_app_role_assignment.owner,
    azuread_application_federated_identity_credential.easyauth,
    azuread_application_identifier_uri.easyauth,
    azuread_application_pre_authorized.azure_cli,
  ]
}

# ---------------------------------------------------------------------------
# The Entra side of Easy Auth (create_entra_app = true, the default). The
# owner, a tenant admin, applies this locally. The CI identities have no
# Microsoft Graph permissions, so infra.yml cannot refresh these resources
# (see README, CI).
# ---------------------------------------------------------------------------

resource "azuread_application" "easyauth" {
  count = var.create_entra_app ? 1 : 0

  display_name     = var.entra_app_display_name
  sign_in_audience = "AzureADMyOrg"
  # The owner, not "whoever runs Terraform", so a run under another identity
  # does not try to rewrite ownership.
  owners = [var.owner_object_id]

  api {
    # v2 access tokens: issuer .../v2.0 (matches openIdIssuer) and aud = the
    # client ID, which Easy Auth accepts.
    requested_access_token_version = 2

    # Always declared, enabled only with easyauth_cli_access_enabled: Entra
    # refuses to delete an enabled scope, so turning CLI access off just
    # disables it.
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
    # azuread_application_identifier_uri owns identifier_uris: api://<client id>
    # cannot be set here without the application referencing itself.
    ignore_changes = [identifier_uris]
  }
}

resource "azuread_service_principal" "easyauth" {
  count = var.create_entra_app ? 1 : 0

  client_id = azuread_application.easyauth[0].client_id
  owners    = [var.owner_object_id]

  # "Assignment required": only assigned principals can sign in or get a token
  # for this app at all.
  app_role_assignment_required = true
}

resource "azuread_app_role_assignment" "owner" {
  count = var.create_entra_app ? 1 : 0

  app_role_id         = "00000000-0000-0000-0000-000000000000" # default access
  principal_object_id = var.owner_object_id
  resource_object_id  = azuread_service_principal.easyauth[0].object_id
}

# The easyauth managed identity as a federated credential of the app
# registration: no client secret exists anywhere.
resource "azuread_application_federated_identity_credential" "easyauth" {
  count = var.create_entra_app && var.easyauth_credential == "federated" ? 1 : 0

  application_id = azuread_application.easyauth[0].id
  display_name   = "easyauth-managed-identity"
  description    = "Container Apps Easy Auth sidecar (user-assigned managed identity)"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = local.entra_v2_issuer
  subject        = azurerm_user_assigned_identity.this["easyauth"].principal_id
}

# ---------------------------------------------------------------------------
# Programmatic owner access for live testing (easyauth_cli_access_enabled):
#   az account get-access-token --resource api://<client id> --query accessToken -o tsv
# then call the app with "Authorization: Bearer <token>".
# ---------------------------------------------------------------------------

resource "azuread_application_identifier_uri" "easyauth" {
  count = local.easyauth_create_cli_access ? 1 : 0

  application_id = azuread_application.easyauth[0].id
  identifier_uri = "api://${azuread_application.easyauth[0].client_id}"
}

# The Azure CLI may request user_impersonation without a consent prompt.
resource "azuread_application_pre_authorized" "azure_cli" {
  count = local.easyauth_create_cli_access ? 1 : 0

  application_id       = azuread_application.easyauth[0].id
  authorized_client_id = local.azure_cli_client_id
  permission_ids       = [local.easyauth_scope_id]
}
