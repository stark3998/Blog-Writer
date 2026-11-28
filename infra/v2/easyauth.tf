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
        # With a public site (site_hosts) the platform lets anonymous requests
        # through: the site hosts are public and the app is the gate for every
        # other host. It authenticates each request itself (owner principal or
        # __Host-bw cookie, owner check, CSRF), sends unauthenticated browser
        # navigations to /.auth/login/aad and answers 401 to the rest. The
        # sidecar still strips client-sent X-MS-CLIENT-PRINCIPAL* headers and
        # still rejects signed-in non-owners (allowedPrincipals below).
        unauthenticatedClientAction = local.easyauth_unauthenticated_action
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

        # Easy Auth's CSRF mitigation rejects cookie-authenticated browser POSTs whose Origin
        # is not in this list or the CORS list (403, sub-status 60), including same-origin
        # writes from the SPA. List only the app's own origins; the app still enforces its own
        # CSRF rule (__Host-bw cookie + Sec-Fetch-Site: same-origin) on every write.
        allowedExternalRedirectUrls = [for host in local.app_hostnames : "https://${host}"]
      }

      httpSettings = {
        requireHttps = true
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.easyauth_client_id != null
      error_message = "Set easyauth_client_id to the Entra app registration's client ID (terraform output easyauth_client_id in infra/v2/entra). Without it the app would be exposed without authentication."
    }

    precondition {
      condition     = var.easyauth_credential == "federated" || contains(local.app_key_vault_secret_names, "easyauth-client-secret")
      error_message = "easyauth_credential = \"client_secret\" needs Key Vault secret 'easyauth-client-secret' set by the owner and listed in key_vault_secrets_set."
    }
  }

}
