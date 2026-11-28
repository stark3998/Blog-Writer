# Blog Writer v2 platform root, applied INTO the existing footprint (owner
# decision 2026-11-26). The v2 app (ca-blog-writer-v2) runs side by side with
# v1 in the existing Container Apps environment; v1 keeps its custom domain
# until a later cutover.
#
# Read here and never managed or changed (data sources only):
#   * the resource group, Log Analytics workspace and Container Apps
#     environment, all shared with v1;
#   * the container registry, shared with another app (admin user left as is);
#   * the v2 Cosmos DB account, database and published-blogs container, owned
#     by infra/v2/data (which also holds the owner's Cosmos role and the lock).
#
# The registry, the workspace and the Cosmos account are read with azapi (a
# plain ARM GET). The azurerm data sources for them also call listCredentials,
# getSharedKeys and listKeys, which would copy a live registry admin password,
# workspace keys and Cosmos account keys into the Terraform state.

data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

# Legacy Consumption-only environment (no workload profiles, no VNet). Its
# logs go to the workspace below through the 'log-analytics' destination
# (tables ContainerAppConsoleLogs_CL and ContainerAppSystemLogs_CL).
data "azurerm_container_app_environment" "this" {
  name                = var.container_app_environment_name
  resource_group_name = data.azurerm_resource_group.this.name
}

data "azapi_resource" "log_analytics" {
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = var.log_analytics_workspace_name
  parent_id = data.azurerm_resource_group.this.id
}

data "azapi_resource" "registry" {
  type      = "Microsoft.ContainerRegistry/registries@2023-07-01"
  name      = var.container_registry_name
  parent_id = data.azurerm_resource_group.this.id

  response_export_values = {
    login_server = "properties.loginServer"
  }
}

data "azapi_resource" "cosmos_account" {
  type      = "Microsoft.DocumentDB/databaseAccounts@2024-11-15"
  name      = var.cosmos_account_name
  parent_id = data.azurerm_resource_group.this.id

  response_export_values = {
    endpoint           = "properties.documentEndpoint"
    disable_local_auth = "properties.disableLocalAuth"
  }
}

# Read only to fail the plan early (with a clear error) if the data root has
# not created the database or the container the portfolio build reads.
data "azapi_resource" "cosmos_database" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15"
  name      = var.cosmos_database_name
  parent_id = data.azapi_resource.cosmos_account.id
}

data "azapi_resource" "cosmos_portfolio_container" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15"
  name      = local.portfolio_cosmos_container
  parent_id = data.azapi_resource.cosmos_database.id
}

# ---------------------------------------------------------------------------
# User-assigned managed identities (new, managed here)
# ---------------------------------------------------------------------------

resource "azurerm_user_assigned_identity" "this" {
  for_each = local.identities

  name                = each.value
  location            = var.location
  resource_group_name = data.azurerm_resource_group.this.name
  tags                = local.tags
}
