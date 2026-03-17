locals {
  acr_name_seed = lower(replace("${var.project_name}${var.environment}${var.location_short}", "-", ""))
}

resource "random_string" "acr_suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = var.log_analytics_workspace_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "this" {
  name                       = var.container_app_environment_name
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  tags                       = var.tags
}

resource "azurerm_container_registry" "this" {
  name                = substr("${local.acr_name_seed}${random_string.acr_suffix.result}", 0, 50)
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = var.acr_sku
  admin_enabled       = true
  tags                = var.tags
}

resource "azurerm_container_app" "portfolio" {
  name                         = var.portfolio_container_app_name
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = var.tags

  ingress {
    external_enabled = true
    target_port      = var.portfolio_target_port

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  dynamic "secret" {
    for_each = merge(var.portfolio_secrets, {
      acr-password = azurerm_container_registry.this.admin_password
    })

    content {
      name  = secret.key
      value = secret.value
    }
  }

  registry {
    server               = azurerm_container_registry.this.login_server
    username             = azurerm_container_registry.this.admin_username
    password_secret_name = "acr-password"
  }

  template {
    min_replicas = var.portfolio_min_replicas
    max_replicas = var.portfolio_max_replicas

    container {
      name   = "portfolio"
      image  = var.portfolio_container_image
      cpu    = var.portfolio_container_cpu
      memory = var.portfolio_container_memory

      dynamic "env" {
        for_each = var.portfolio_env_vars

        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.portfolio_secret_env_vars

        content {
          name        = env.key
          secret_name = env.value
        }
      }
    }
  }
}

resource "azurerm_container_app" "this" {
  name                         = var.container_app_name
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = var.tags

  ingress {
    external_enabled = true
    target_port      = var.target_port

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  dynamic "secret" {
    for_each = merge(var.container_app_secrets, {
      acr-password = azurerm_container_registry.this.admin_password
    })

    content {
      name  = secret.key
      value = secret.value
    }
  }

  registry {
    server               = azurerm_container_registry.this.login_server
    username             = azurerm_container_registry.this.admin_username
    password_secret_name = "acr-password"
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "webapp"
      image  = var.container_image
      cpu    = var.container_cpu
      memory = var.container_memory

      dynamic "env" {
        for_each = var.container_app_env_vars

        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.container_app_secret_env_vars

        content {
          name        = env.key
          secret_name = env.value
        }
      }

      startup_probe {
        transport               = "HTTP"
        path                    = "/api/health"
        port                    = 8080
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 30
      }

      liveness_probe {
        transport               = "HTTP"
        path                    = "/api/health"
        port                    = 8080
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }
}
