# Key Vault in RBAC mode with purge protection. New and managed here.
# Terraform never creates secret values; see local.key_vault_secrets for the
# expected names and the README for the owner commands.

resource "azurerm_key_vault" "this" {
  name                = local.names.key_vault
  location            = var.location
  resource_group_name = data.azurerm_resource_group.this.name
  tenant_id           = local.tenant_id
  sku_name            = "standard"

  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 90 # cannot be changed once purge protection is on

  enabled_for_deployment          = false
  enabled_for_disk_encryption     = false
  enabled_for_template_deployment = false

  # The Consumption environment has no VNet, so the app reaches the vault over
  # the public endpoint; access is Entra ID + RBAC only.
  public_network_access_enabled = true

  tags = local.tags
}
