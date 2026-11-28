# Private Blob storage, new and managed here: media (drafts, carousels,
# exports), published (assets of published articles; the portfolio build
# downloads them with Entra ID, so there is no anonymous access) and backups
# (daily dumps, v1 export).

resource "azurerm_storage_account" "this" {
  name                     = local.names.storage_account
  resource_group_name      = data.azurerm_resource_group.this.name
  location                 = var.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = var.storage_replication_type
  access_tier              = "Hot"

  # Lab policy: HTTPS only (deny); public blob access is audited, keep it off.
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  # Entra ID only.
  shared_access_key_enabled        = false
  default_to_oauth_authentication  = true
  local_user_enabled               = false
  sftp_enabled                     = false
  cross_tenant_replication_enabled = false

  # No VNet: the app and GitHub runners use the public endpoint with Entra ID.
  public_network_access_enabled = true

  blob_properties {
    versioning_enabled = true

    # Soft-deleted blobs and containers are removed after 30 days.
    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  tags = local.tags
}

resource "azurerm_storage_container" "this" {
  for_each = toset(local.storage_containers)

  name                  = each.value
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id

  # Daily JSONL dumps: deleted 35 days after they were written. Scoped to the
  # dump prefix so backups/v1-export/ survives until the Phase 9 migration.
  rule {
    name    = "expire-daily-dumps"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["${azurerm_storage_container.this["backups"].name}/${local.backup_dump_prefix}"]
    }

    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 35
      }
    }
  }

  # Previous versions and snapshots in every container: deleted after 30 days.
  # (Soft-deleted blobs are purged by the 30-day delete retention policy.)
  rule {
    name    = "expire-old-versions"
    enabled = true

    filters {
      blob_types = ["blockBlob"]
    }

    actions {
      version {
        delete_after_days_since_creation = 30
      }

      snapshot {
        delete_after_days_since_creation_greater_than = 30
      }
    }
  }
}
