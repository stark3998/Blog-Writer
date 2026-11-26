# The single always-on Blog Writer v2 container app: one revision, exactly one
# replica, 1 vCPU / 2 GiB. deploy.yml owns the image (az containerapp update
# --image); Terraform owns everything else.

locals {
  # Non-secret configuration. Secrets come from Key Vault references below.
  app_env = merge(
    {
      # Managed identity selection (two user-assigned identities are attached).
      AZURE_CLIENT_ID = azurerm_user_assigned_identity.this["app"].client_id
      AZURE_TENANT_ID = local.tenant_id

      # In-app fail-closed owner check (in addition to Easy Auth).
      OWNER_OID = var.owner_object_id

      APP_ENV = "production"
      PORT    = tostring(var.target_port)

      COSMOS_ENDPOINT = azurerm_cosmosdb_account.this.endpoint
      COSMOS_DATABASE = azurerm_cosmosdb_sql_database.this.name

      BLOB_ENDPOINT            = azurerm_storage_account.this.primary_blob_endpoint
      BLOB_CONTAINER_MEDIA     = azurerm_storage_container.this["media"].name
      BLOB_CONTAINER_PUBLISHED = azurerm_storage_container.this["published"].name
      BLOB_CONTAINER_BACKUPS   = azurerm_storage_container.this["backups"].name
      BACKUP_DUMP_PREFIX       = local.backup_dump_prefix

      KEY_VAULT_URI = azurerm_key_vault.this.vault_uri

      # Azure OpenAI GA v1 endpoint (Responses API, tool calling, web_search).
      AZURE_OPENAI_ENDPOINT = local.foundry_openai_endpoint
      AZURE_OPENAI_BASE_URL = local.foundry_openai_v1_base_url

      # Read by anthropic.AnthropicFoundry / AsyncAnthropicFoundry
      # (https://<resource>.services.ai.azure.com/anthropic/); auth is an Entra
      # token for https://ai.azure.com/.default. Do not also set
      # ANTHROPIC_FOUNDRY_BASE_URL: the two are mutually exclusive.
      ANTHROPIC_FOUNDRY_RESOURCE = local.foundry_subdomain
      MODEL_DEPLOYMENT_CLAUDE    = var.claude_deployment_name

      WEBIQ_ENABLED = tostring(var.enable_webiq)

      # Portfolio build dispatch target (the PAT is a Key Vault secret).
      PORTFOLIO_REPOSITORY = "${local.portfolio_owner}/${var.portfolio_repo}"
      PORTFOLIO_REF        = var.portfolio_branch
    },
    # MODEL_DEPLOYMENT_SOL / _ASTRA / _LUNA / _EMBEDDING (+ _SOL_FALLBACK).
    { for name, deployment in local.foundry_deployments : deployment.env => name },
    var.enable_image_generation ? {
      IMAGE_OPENAI_BASE_URL  = local.image_openai_v1_base_url
      MODEL_DEPLOYMENT_IMAGE = one(azurerm_cognitive_deployment.image[*].name)
    } : {},
  )
}

resource "azurerm_container_app" "app" {
  name                         = local.names.container_app
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type = "UserAssigned"
    identity_ids = [
      azurerm_user_assigned_identity.this["app"].id,
      # Used only by the Easy Auth sidecar (federated credential assertion).
      azurerm_user_assigned_identity.this["easyauth"].id,
    ]
  }

  # Image pulls use the app identity (AcrPull); no registry passwords.
  registry {
    server   = azurerm_container_registry.this.login_server
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
    # deploy.yml sets the image (SHA tag). Terraform keeps the live image on
    # every later update instead of rolling back to var.container_image.
    ignore_changes = [template[0].container[0].image]
  }

  # Registry pull and Key Vault references must be authorised before the first
  # revision is provisioned.
  depends_on = [
    azurerm_role_assignment.app_acr_pull,
    azurerm_role_assignment.app_key_vault_secrets_user,
  ]
}
