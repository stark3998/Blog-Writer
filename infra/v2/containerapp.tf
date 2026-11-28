# The Blog Writer v2 container app: NEW and side by side with v1 in the
# existing legacy Consumption-only environment. One revision, exactly one
# replica, 1 vCPU / 2 GiB for the app plus, with site_hosts, the portfolio
# site as a second container (0.25 vCPU / 0.5 GiB by default) that the app
# serves on the site hosts. The deploys (az containerapp update --image) own
# the images; Terraform owns everything else, including every environment
# variable.

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

      # The public site the publish step verifies (and the gates link to).
      BW_PUBLISH_SITE_ORIGIN     = local.site_origin
      BW_GATES_SITE_ORIGIN       = local.site_origin
      BW_RENDER_CHROMIUM_SANDBOX = var.render_chromium_sandbox ? "1" : "0"
      BW_AI_BRAKE_DAILY_CAP_USD  = tostring(var.ai_brake_daily_cap_usd)
    },
    # Portfolio in this replica (site_hosts): the gateway proxies the site hosts to
    # the portfolio container, and a publish revalidates and verifies the page there
    # (no GitHub build, dispatch or PAT). Without it: the GitHub Pages dispatch.
    local.site_enabled ? {
      BW_SITE_HOSTS            = join(",", var.site_hosts)
      BW_SITE_UPSTREAM         = local.site_upstream
      BW_PUBLISH_SITE_UPSTREAM = local.site_upstream
      } : {
      BW_PUBLISH_PORTFOLIO_REPOSITORY = "${local.portfolio_owner}/${var.portfolio_repo}"
      BW_PUBLISH_PORTFOLIO_REF        = var.portfolio_branch
      BW_PUBLISH_PORTFOLIO_WORKFLOW   = var.portfolio_workflow
    },
    # BW_AI_DEPLOYMENT_SOL / _ASTRA / _LUNA / _EMBEDDING.
    { for name, deployment in local.foundry_deployments : deployment.env => name if deployment.env != null },
    # The notify settings refuse a subject without its private key (they must
    # be set together), so the subject follows the Key Vault secret.
    contains(local.app_key_vault_secret_names, "vapid-private-key") ? { BW_NOTIFY_VAPID_SUBJECT = "mailto:${var.alert_email}" } : {},
    var.linkedin_client_id != null ? { BW_LINKEDIN_CLIENT_ID = var.linkedin_client_id } : {},
    var.vault_primary_kid != null ? { BW_VAULT_PRIMARY = var.vault_primary_kid } : {},
    local.webiq_endpoint != null ? { BW_RESEARCH_WEBIQ_ENDPOINT = local.webiq_endpoint, BW_RESEARCH_WEBIQ_SCOPE = var.webiq_scope } : {},
    # AI illustrations (gpt-image-2 on the optional East US 2 account): Task.IMAGE route.
    var.enable_image_generation ? {
      BW_AI_IMAGE_BASE_URL   = local.image_openai_v1_base_url
      BW_AI_DEPLOYMENT_IMAGE = one(azurerm_cognitive_deployment.image[*].name)
    } : {},
  )

  # The portfolio container: read-only data access with its own identity, no
  # secrets. It binds 127.0.0.1 (PORTFOLIO_BIND), so only this replica reaches it.
  portfolio_env = {
    AZURE_CLIENT_ID      = azurerm_user_assigned_identity.this["portfolio_build"].client_id
    COSMOS_ENDPOINT      = local.cosmos_endpoint
    COSMOS_DATABASE      = var.cosmos_database_name
    COSMOS_CONTAINER     = local.portfolio_cosmos_container
    BLOB_ENDPOINT        = azurerm_storage_account.this.primary_blob_endpoint
    BLOB_CONTAINER       = azurerm_storage_container.this["published"].name
    NEXT_PUBLIC_SITE_URL = local.site_origin
    PORTFOLIO_BIND       = "127.0.0.1"
    PORT                 = tostring(var.portfolio_port)
  }
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
    identity_ids = concat([
      azurerm_user_assigned_identity.this["app"].id,
      # Used only by the Easy Auth sidecar (federated credential assertion).
      azurerm_user_assigned_identity.this["easyauth"].id,
      ],
      # The portfolio container's identity: read-only on published-blogs and the
      # "published" blobs (rbac.tf), selected there with AZURE_CLIENT_ID.
      local.site_enabled ? [azurerm_user_assigned_identity.this["portfolio_build"].id] : [],
    )
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

    # Only the app's own origins. Same-origin requests ignore CORS, but Easy Auth's CSRF
    # mitigation rejects cookie-authenticated browser POSTs whose Origin is in neither this
    # list nor login.allowedExternalRedirectUrls (403, sub-status 60). No other origin is added.
    cors {
      allowed_origins = [for host in local.app_hostnames : "https://${host}"]
      # Azure's default; stated so plans stay clean. Only the app's own origins are allowed.
      allowed_headers = ["*"]
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

    # The portfolio site (Next.js standalone server, the portfolio repository).
    # It listens on 127.0.0.1 only, so it has no probes (the kubelet probes the
    # pod address): the app's probes gate the replica, and the portfolio is up
    # long before the app finishes its own startup. The app's gateway answers
    # 502 for the site hosts while it is not.
    dynamic "container" {
      for_each = local.site_enabled ? [var.portfolio_container_image] : []

      content {
        name   = "portfolio"
        image  = container.value
        cpu    = var.portfolio_cpu
        memory = var.portfolio_memory

        dynamic "env" {
          for_each = local.portfolio_env

          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  lifecycle {
    # deploy sets the images (SHA tags): Blog Writer's deploy the app container,
    # the portfolio repository's deploy the portfolio container. Terraform keeps
    # the live images on every later update.
    ignore_changes = [template[0].container[0].image, template[0].container[1].image]

    precondition {
      condition     = !local.site_enabled || var.portfolio_container_image != null
      error_message = "site_hosts needs portfolio_container_image (the first portfolio image; the portfolio deploy owns it afterwards)."
    }
  }

  # Registry pull and Key Vault references must be authorised before a revision
  # that needs them is provisioned.
  depends_on = [
    azurerm_role_assignment.app_acr_pull,
    azurerm_role_assignment.app_key_vault_secrets_user,
  ]
}
