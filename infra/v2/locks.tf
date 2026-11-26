# CanNotDelete locks on the resources that hold durable data or configuration.
# A lock also blocks deleting their child and extension resources through ARM
# (Cosmos containers and SQL role assignments, model deployments, blob
# containers, Azure role assignments scoped to them), so a change that removes
# or replaces one of those needs enable_locks = false first. Locks are
# created/removed by the owner's local apply: Contributor (ci_infra) cannot
# write Microsoft.Authorization/locks.

locals {
  locked_resources = merge(
    {
      cosmos    = azurerm_cosmosdb_account.this.id
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
