data "azurerm_client_config" "current" {}

# The existing v1 resource group (lab policy requires Purpose / Owner /
# Expiry Date tags on resource groups; this root does not touch them).
data "azurerm_resource_group" "existing" {
  name = var.resource_group_name
}

locals {
  subscription_id = data.azurerm_client_config.current.subscription_id
  suffix          = coalesce(var.name_suffix, substr(sha256("${local.subscription_id}/${var.resource_group_name}/v2-data"), 0, 6))
  account_name    = "cosmos-${var.name_prefix}-${local.suffix}"

  tags = merge(var.extra_tags, {
    app        = "blog-writer"
    generation = "v2"
    managed_by = "terraform"
    tf_root    = "infra/v2/data"
  })

  database_name = "blog-writer"

  # Paths never worth indexing (vectors and large text). Excluding a missing
  # path is harmless, so every container gets the same base list.
  excluded_paths = ["/embedding/*", "/markdown/*", "/htmlContent/*", "/mdxContent/*"]

  # Every v2 container (plan: Data section). default_ttl: null = TTL off,
  # -1 = on with no default (per-item ttl works), n = seconds since last write.
  # TTL is garbage collection only; the app still checks an explicit expiresAt.
  # extra_excluded_paths: large text and reports nobody filters or sorts on
  # (style guide body, voice sample text, post texts, pasted comments, gate
  # reports, served-by records). The app refuses queries on them: keep
  # server/src/blogwriter/db/base.py UNINDEXED_FIELDS equal to these lists.
  containers = {
    "settings"         = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "stories"          = { partition_key = "/id", default_ttl = null, extra_excluded_paths = ["/body/*", "/text/*"] }
    "ideas"            = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "research"         = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "articles"         = { partition_key = "/id", default_ttl = null, extra_excluded_paths = ["/gates/*", "/servedBy/*"] }
    "article-versions" = { partition_key = "/articleId", default_ttl = null, extra_excluded_paths = [] }
    "posts"            = { partition_key = "/id", default_ttl = null, extra_excluded_paths = ["/payload/*", "/displayText/*", "/finalText/*", "/aiDraftText/*"] }
    "sources"          = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "signals"          = { partition_key = "/sourceId", default_ttl = 7776000, extra_excluded_paths = [] } # 90 days
    "jobs"             = { partition_key = "/id", default_ttl = -1, extra_excluded_paths = ["/events/*"] }
    "usage"            = { partition_key = "/month", default_ttl = 34214400, extra_excluded_paths = [] }       # ~13 months
    "engagement"       = { partition_key = "/id", default_ttl = -1, extra_excluded_paths = ["/pastedText/*"] } # 48 h per-item ttl on third-party text
    "ephemeral"        = { partition_key = "/id", default_ttl = 86400, extra_excluded_paths = [] }             # OAuth states etc.
    # published-blogs v2 contract: id == slug, partition key /id (the portfolio
    # point-reads item(slug, slug)). Never change this partition key.
    "published-blogs"         = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "published-blogs-staging" = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
  }

  cosmos_data_contributor_role = "00000000-0000-0000-0000-000000000002"
}

resource "azurerm_cosmosdb_account" "this" {
  name                = local.account_name
  location            = data.azurerm_resource_group.existing.location
  resource_group_name = data.azurerm_resource_group.existing.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = data.azurerm_resource_group.existing.location
    failover_priority = 0
    zone_redundant    = false
  }

  # Free point-in-time restore for the last 7 days.
  backup {
    type = "Continuous"
    tier = "Continuous7Days"
  }

  # Entra ID only from day one: no account keys are usable.
  local_authentication_enabled       = false
  access_key_metadata_writes_enabled = false
  minimal_tls_version                = "Tls12"

  # No VNet in this lab setup; the app and the portfolio build use Entra ID
  # tokens against the public endpoint.
  public_network_access_enabled = true

  automatic_failover_enabled = false
  free_tier_enabled          = false
  analytical_storage_enabled = false

  tags = local.tags
}

resource "azurerm_cosmosdb_sql_database" "this" {
  name                = local.database_name
  resource_group_name = data.azurerm_resource_group.existing.name
  account_name        = azurerm_cosmosdb_account.this.name
  # Serverless: no throughput settings.
}

resource "azurerm_cosmosdb_sql_container" "this" {
  for_each = local.containers

  name                  = each.key
  resource_group_name   = data.azurerm_resource_group.existing.name
  account_name          = azurerm_cosmosdb_account.this.name
  database_name         = azurerm_cosmosdb_sql_database.this.name
  partition_key_paths   = [each.value.partition_key]
  partition_key_kind    = "Hash"
  partition_key_version = 2
  default_ttl           = each.value.default_ttl

  indexing_policy {
    indexing_mode = "consistent"

    included_path {
      path = "/*"
    }

    dynamic "excluded_path" {
      for_each = concat(local.excluded_paths, each.value.extra_excluded_paths)

      content {
        path = excluded_path.value
      }
    }
  }
}

# Key auth is disabled, so the owner needs a data-plane role to run the
# migration, restore drills and ad-hoc queries.
resource "azurerm_cosmosdb_sql_role_assignment" "owner_data_contributor" {
  resource_group_name = data.azurerm_resource_group.existing.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/${local.cosmos_data_contributor_role}"
  principal_id        = var.owner_object_id
  scope               = "${azurerm_cosmosdb_account.this.id}/dbs/${azurerm_cosmosdb_sql_database.this.name}"
}

resource "azurerm_management_lock" "cosmos" {
  count = var.enable_lock ? 1 : 0

  name       = "cannot-delete-v2-data"
  scope      = azurerm_cosmosdb_account.this.id
  lock_level = "CanNotDelete"
  notes      = "Blog Writer v2 data. Remove deliberately (Owner/User Access Administrator) before any destroy."
}

check "cosmos_local_auth_disabled" {
  assert {
    condition     = azurerm_cosmosdb_account.this.local_authentication_enabled == false
    error_message = "Cosmos DB local (key) authentication must stay disabled."
  }
}

check "published_blogs_contract" {
  assert {
    condition = (
      azurerm_cosmosdb_sql_container.this["published-blogs"].partition_key_paths == tolist(["/id"]) &&
      azurerm_cosmosdb_sql_container.this["published-blogs-staging"].partition_key_paths == tolist(["/id"])
    )
    error_message = "published-blogs containers must keep partition key /id (the portfolio point-reads item(slug, slug))."
  }
}
