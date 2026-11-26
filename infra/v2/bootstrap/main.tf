data "azurerm_client_config" "current" {}

locals {
  required_tags = {
    "Purpose"     = var.tag_purpose
    "Owner"       = var.tag_owner
    "Expiry Date" = var.tag_expiry_date
  }

  tags = merge(var.extra_tags, {
    app        = "blog-writer"
    generation = "v2"
    component  = "terraform-state"
    managed_by = "terraform"
  }, local.required_tags)

  # Stable, non-secret suffix (like Bicep's uniqueString) so names are globally
  # unique without committing anything subscription-specific.
  suffix               = substr(sha256("${data.azurerm_client_config.current.subscription_id}/${var.resource_group_name}"), 0, 6)
  storage_account_name = coalesce(var.storage_account_name, "stbw2tf${local.suffix}")

  # Role assignments on the container scope (least privilege for the azurerm backend).
  tfstate_container_scope = "${azurerm_storage_account.state.id}/blobServices/default/containers/${azurerm_storage_container.tfstate.name}"

  storage_blob_data_contributor_role_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
}

resource "azurerm_resource_group" "state" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_storage_account" "state" {
  name                     = local.storage_account_name
  resource_group_name      = azurerm_resource_group.state.name
  location                 = azurerm_resource_group.state.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = var.replication_type
  access_tier              = "Hot"

  # Lab policy: HTTPS only (deny) and public blob access audited.
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  # Entra ID only: no account keys, no SAS, no SFTP local users.
  shared_access_key_enabled        = false
  default_to_oauth_authentication  = true
  local_user_enabled               = false
  sftp_enabled                     = false
  cross_tenant_replication_enabled = false

  # CI runners and laptops reach the account over the internet; access is
  # still Entra-ID-only (Storage Blob Data Contributor on the container).
  public_network_access_enabled = true

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = var.soft_delete_retention_days
    }

    container_delete_retention_policy {
      days = var.soft_delete_retention_days
    }
  }

  tags = local.tags
}

resource "azurerm_storage_container" "tfstate" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

# The person running this root (Terraform state for the main root is read and
# written with their Entra ID token).
resource "azurerm_role_assignment" "deployer_state" {
  scope              = local.tfstate_container_scope
  role_definition_id = local.storage_blob_data_contributor_role_id
  principal_id       = data.azurerm_client_config.current.object_id
  description        = "Blog Writer v2: Terraform state access for the deployer"
}

# Optional extra principals, typically the ci_infra managed identity created by
# the main root (infra.yml runs plan/apply with it).
resource "azurerm_role_assignment" "extra_state" {
  for_each = var.state_contributor_principal_ids

  scope              = local.tfstate_container_scope
  role_definition_id = local.storage_blob_data_contributor_role_id
  principal_id       = each.value
  description        = "Blog Writer v2: Terraform state access for ${each.key}"
}

resource "azurerm_management_lock" "state" {
  count = var.enable_lock ? 1 : 0

  name       = "no-delete"
  scope      = azurerm_storage_account.state.id
  lock_level = "CanNotDelete"
  notes      = "Holds the Blog Writer v2 Terraform state. Set enable_lock = false to remove (needs Owner or User Access Administrator)."
}
