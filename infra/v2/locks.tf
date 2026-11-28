# CanNotDelete locks on the resources created here that hold durable data or
# configuration: storage, Foundry and Key Vault. (The Cosmos DB lock lives in
# infra/v2/data.) A lock also blocks deleting their child and extension
# resources through ARM (model deployments, blob containers, Azure role
# assignments scoped to them), so a change that removes or replaces one of
# those needs enable_locks = false first. Locks are created/removed by the
# owner's local apply: Contributor (ci_infra) cannot write
# Microsoft.Authorization/locks.

locals {
  locked_resources = merge(
    {
      storage   = azurerm_storage_account.this.id
      foundry   = azurerm_cognitive_account.foundry.id
      key_vault = azurerm_key_vault.this.id
    },
    var.enable_image_generation ? { foundry_images = one(azurerm_cognitive_account.images[*].id) } : {},
  )
}

resource "azurerm_management_lock" "no_delete" {
  for_each = var.enable_locks ? local.locked_resources : {}

  name       = "no-delete"
  scope      = each.value
  lock_level = "CanNotDelete"
  notes      = "Blog Writer v2 durable data/config (${each.key}). Remove by applying enable_locks = false as Owner/User Access Administrator."
}
