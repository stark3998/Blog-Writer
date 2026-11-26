# Blog Writer v2 platform: its own resource group and everything in it.
# Nothing here references v1 (its resource group, its ACR or its Cosmos
# account, all shared with other projects).

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags # includes Purpose, Owner and "Expiry Date" (lab deny policy)
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "this" {
  name                = local.names.log_analytics
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days

  # Ingestion comes only through diagnostic settings (no workspace shared key
  # is handed to the Container Apps environment), so key-based auth is off.
  local_authentication_enabled = false

  tags = local.tags
}

# ---------------------------------------------------------------------------
# User-assigned managed identities
# ---------------------------------------------------------------------------

resource "azurerm_user_assigned_identity" "this" {
  for_each = local.identities

  name                = each.value
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

# ---------------------------------------------------------------------------
# Container Apps environment: workload-profiles type with the Consumption
# profile only. No dedicated profile, VNet/private endpoint, premium ingress or
# maintenance window (each costs >= $73/month).
# ---------------------------------------------------------------------------

resource "azurerm_container_app_environment" "this" {
  name                = local.names.environment
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  # Logs flow through Azure Monitor diagnostic settings (below) instead of the
  # 'log-analytics' destination, which would need the workspace shared key.
  logs_destination = "azure-monitor"

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = local.tags
}

resource "azurerm_monitor_diagnostic_setting" "environment" {
  name                           = "to-log-analytics"
  target_resource_id             = azurerm_container_app_environment.this.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.this.id
  log_analytics_destination_type = "Dedicated" # ContainerAppConsoleLogs / ContainerAppSystemLogs tables

  enabled_log {
    category = "ContainerAppConsoleLogs"
  }

  enabled_log {
    category = "ContainerAppSystemLogs"
  }

  # ContainerAppHTTPLogs is deliberately NOT enabled: platform access logs
  # carry full request URLs, including OAuth callback query strings.

  enabled_metric {
    category = "AllMetrics"
  }
}
