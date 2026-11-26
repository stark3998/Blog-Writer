# New, dedicated Cosmos DB account for v2 (serverless, Entra ID only). v1 data
# lives in a Cosmos account shared with another project; it is read-only
# migration input and is never referenced here.

locals {
  cosmos_database_name = "blog-writer"

  # Paths never worth indexing (vectors and large text). Excluding a missing
  # path is harmless, so every container gets the same base list.
  cosmos_excluded_paths = ["/embedding/*", "/markdown/*", "/htmlContent/*", "/mdxContent/*"]

  # Every v2 container (plan: Data section). default_ttl: null = TTL off,
  # -1 = on with no default (per-item ttl works), n = seconds since last write.
  # TTL is garbage collection only; the app still checks an explicit expiresAt.
  cosmos_containers = {
    "settings"         = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "stories"          = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "ideas"            = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "research"         = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "articles"         = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "article-versions" = { partition_key = "/articleId", default_ttl = null, extra_excluded_paths = [] }
    "posts"            = { partition_key = "/id", default_ttl = null, extra_excluded_paths = ["/payload/*"] }
    "sources"          = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "signals"          = { partition_key = "/sourceId", default_ttl = 7776000, extra_excluded_paths = [] } # 90 days
    "jobs"             = { partition_key = "/id", default_ttl = -1, extra_excluded_paths = ["/events/*"] } # capped events array
    "usage"            = { partition_key = "/month", default_ttl = 34214400, extra_excluded_paths = [] }   # 396 days ~ 13 months
    "engagement"       = { partition_key = "/id", default_ttl = -1, extra_excluded_paths = [] }            # 48 h per-item ttl on third-party text
    "ephemeral"        = { partition_key = "/id", default_ttl = 86400, extra_excluded_paths = [] }         # OAuth states etc.; items set shorter ttl
    # published-blogs v2 contract: id == slug, partition key /id (the portfolio
    # point-reads item(slug, slug)). Never change this partition key.
    "published-blogs"         = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
    "published-blogs-staging" = { partition_key = "/id", default_ttl = null, extra_excluded_paths = [] }
  }

  # The only containers the portfolio build may read.
  portfolio_readable_containers = ["published-blogs", "published-blogs-staging"]
}

resource "azurerm_cosmosdb_account" "this" {
  name                = local.names.cosmos_account
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.this.location
    failover_priority = 0
    zone_redundant    = false
  }

  # Free point-in-time restore for the last 7 days.
  backup {
    type = "Continuous"
    tier = "Continuous7Days"
  }

  # Entra ID only from day one. (local_authentication_enabled = false is the
  # azurerm 4.x replacement for the deprecated local_authentication_disabled.)
  local_authentication_enabled       = false
  access_key_metadata_writes_enabled = false
  minimal_tls_version                = "Tls12"

  # No VNet; the app and the portfolio build (GitHub runners) use the public
  # endpoint with Entra ID tokens.
  public_network_access_enabled = true

  automatic_failover_enabled = false
  free_tier_enabled          = false
  analytical_storage_enabled = false

  tags = local.tags
}

resource "azurerm_cosmosdb_sql_database" "this" {
  name                = local.cosmos_database_name
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  # Serverless: no throughput settings.
}

resource "azurerm_cosmosdb_sql_container" "this" {
  for_each = local.cosmos_containers

  name                  = each.key
  resource_group_name   = azurerm_resource_group.this.name
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
      for_each = concat(local.cosmos_excluded_paths, each.value.extra_excluded_paths)

      content {
        path = excluded_path.value
      }
    }
  }
}
