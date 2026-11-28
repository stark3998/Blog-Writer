# The Blog Writer v2 container app: NEW and side by side with v1 in the
# existing legacy Consumption-only environment. One revision, exactly one
# replica, 1 vCPU / 2 GiB. deploy (az containerapp update --image) owns the
# image; Terraform owns everything else, including every environment variable.

locals {
  # Non-secret configuration, using the names the app reads (BW_*; see the
  # pydantic-settings classes in server/src/blogwriter). Secrets come from Key
  # Vault references below. Not set here on purpose: BW_BUILD_SHA (baked into
  # the image by the build) and BW_SPA_DIR (set by the image).
  app_env = merge(
    {
      # DefaultAzureCredential picks the app identity (two user-assigned
      # identities are attached; the other one is only for Easy Auth).
      AZURE_CLIENT_ID = azurerm_user_assigned_identity.this["app"].client_id

      # Core: prod is strict (fails closed without the owner, cookie key and
      # an https public origin).
      BW_ENV           = "prod"
      BW_OWNER_OID     = var.owner_object_id
      BW_PUBLIC_ORIGIN = local.public_origin

      # Cosmos DB from infra/v2/data (Entra ID only).
      BW_COSMOS_ENDPOINT = local.cosmos_endpoint
      BW_COSMOS_DATABASE = var.cosmos_database_name

      # Azure OpenAI GA v1 endpoint (Responses API) and Claude on the same
      # Foundry resource (https://<resource>.services.ai.azure.com/anthropic/,
      # Entra scope https://ai.azure.com/.default).
      BW_AI_OPENAI_BASE_URL    = local.foundry_openai_v1_base_url
      BW_AI_ANTHROPIC_RESOURCE = local.foundry_subdomain
      BW_AI_DEPLOYMENT_OPUS    = var.claude_deployment_name

      # Private Blob storage (Entra ID only).
      BW_STORAGE_BLOB_ENDPOINT       = azurerm_storage_account.this.primary_blob_endpoint
      BW_STORAGE_CONTAINER_MEDIA     = azurerm_storage_container.this["media"].name
      BW_STORAGE_CONTAINER_PUBLISHED = azurerm_storage_container.this["published"].name
      BW_STORAGE_CONTAINER_BACKUPS   = azurerm_storage_container.this["backups"].name

      # Portfolio build dispatch target (the PAT is a Key Vault secret) and the
      # public site the publish step verifies.
      BW_PUBLISH_PORTFOLIO_REPOSITORY = "${local.portfolio_owner}/${var.portfolio_repo}"
      BW_PUBLISH_PORTFOLIO_REF        = var.portfolio_branch
      BW_PUBLISH_PORTFOLIO_WORKFLOW   = var.portfolio_workflow
      BW_PUBLISH_SITE_ORIGIN          = var.publish_site_origin
      BW_GATES_SITE_ORIGIN            = var.publish_site_origin
      BW_RENDER_CHROMIUM_SANDBOX      = var.render_chromium_sandbox ? "1" : "0"
    },
    # BW_AI_DEPLOYMENT_SOL / _ASTRA / _LUNA / _EMBEDDING.
    { for name, deployment in local.foundry_deployments : deployment.env => name if deployment.env != null },
    # The notify settings refuse a subject without its private key (they must
    # be set together), so the subject follows the Key Vault secret.
    contains(local.app_key_vault_secret_names, "vapid-private-key") ? { BW_NOTIFY_VAPID_SUBJECT = "mailto:${var.alert_email}" } : {},
    var.linkedin_client_id != null ? { BW_LINKEDIN_CLIENT_ID = var.linkedin_client_id } : {},
    var.vault_primary_kid != null ? { BW_VAULT_PRIMARY = var.vault_primary_kid } : {},
    local.webiq_endpoint != null ? { BW_RESEARCH_WEBIQ_ENDPOINT = local.webiq_endpoint, BW_RESEARCH_WEBIQ_SCOPE = var.webiq_scope } : {},
  )
}

resource "azurerm_container_app" "app" {
  name                         = var.container_app_name
  resource_group_name          = data.azurerm_resource_group.this.name
  container_app_environment_id = data.azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = local.tags

  # No workload_profile_name: the environment is a legacy Consumption-only
  # environment without workload profiles.

  identity {
    type = "UserAssigned"
    identity_ids = [
      azurerm_user_assigned_identity.this["app"].id,
      # Used only by the Easy Auth sidecar (federated credential assertion).
      azurerm_user_assigned_identity.this["easyauth"].id,
    ]
  }

  # Image pulls from the shared registry use the app identity (AcrPull); no
  # registry passwords.
  registry {
    server   = local.acr_login_server
    identity = azurerm_user_assigned_identity.this["app"].id
  }

  ingress {
    external_enabled           = true
    target_port                = var.target_port
    transport                  = "auto"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  # Versionless Key Vault references (ACA follows the latest version), read by
  # the app identity. Only secrets the owner has set (key_vault_secrets_set).
  dynamic "secret" {
    for_each = local.app_key_vault_secret_names

    content {
      name                = secret.value
      key_vault_secret_id = "${azurerm_key_vault.this.vault_uri}secrets/${secret.value}"
      identity            = azurerm_user_assigned_identity.this["app"].id
    }
  }

  # Easy Auth with a managed identity as federated credential: the sidecar
  # recognises this secret name and uses the identity whose CLIENT ID it holds
  # (not a secret value). Same mechanism as Azure-Samples/containerapps-builtinauth-bicep.
  dynamic "secret" {
    for_each = var.easyauth_credential == "federated" ? [local.easyauth_fic_secret_name] : []

    content {
      name  = secret.value
      value = azurerm_user_assigned_identity.this["easyauth"].client_id
    }
  }

  template {
    min_replicas                     = 1
    max_replicas                     = 1
    termination_grace_period_seconds = 120

    container {
      name   = "app"
      image  = var.container_image
      cpu    = 1
      memory = "2Gi"

      dynamic "env" {
        for_each = local.app_env

        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.app_secret_env

        content {
          name        = env.key
          secret_name = env.value
        }
      }

      # Process check only: never depends on Cosmos, Foundry or the network.
      liveness_probe {
        transport               = "TCP"
        port                    = var.target_port
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }

      # /api/health is served by the real image and excluded from Easy Auth.
      # TCP while the placeholder image runs (http_health_probes_enabled = false
      # for the first applies; see README).
      readiness_probe {
        transport               = var.http_health_probes_enabled ? "HTTP" : "TCP"
        port                    = var.target_port
        path                    = var.http_health_probes_enabled ? "/api/health" : null
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }

      # Up to 5 minutes for boot (claims recovery, Chromium warm-up).
      startup_probe {
        transport               = var.http_health_probes_enabled ? "HTTP" : "TCP"
        port                    = var.target_port
        path                    = var.http_health_probes_enabled ? "/api/health" : null
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 30
      }
    }
  }

  lifecycle {
    # deploy sets the image (SHA tag). Terraform keeps the live image on every
    # later update instead of rolling back to var.container_image.
    ignore_changes = [template[0].container[0].image]
  }

  # Registry pull and Key Vault references must be authorised before a revision
  # that needs them is provisioned.
  depends_on = [
    azurerm_role_assignment.app_acr_pull,
    azurerm_role_assignment.app_key_vault_secrets_user,
  ]
}
