# Azure RBAC and Cosmos DB data-plane role assignments. Every principal is a
# managed identity created here or the owner; no keys or client secrets.
# (Lab policy only denies the 'BizTalk Contributor' role.)

# ---------------------------------------------------------------------------
# App identity: Foundry User, Cosmos Data Contributor (database scope),
# Storage Blob Data Contributor, Key Vault Secrets User, AcrPull.
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "app_acr_pull" {
  scope              = azurerm_container_registry.this.id
  role_definition_id = local.role_definition_ids.acr_pull
  principal_id       = azurerm_user_assigned_identity.this["app"].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "app_key_vault_secrets_user" {
  scope              = azurerm_key_vault.this.id
  role_definition_id = local.role_definition_ids.key_vault_secrets_user
  principal_id       = azurerm_user_assigned_identity.this["app"].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "app_blob_data_contributor" {
  scope              = azurerm_storage_account.this.id
  role_definition_id = local.role_definition_ids.storage_blob_data_contributor
  principal_id       = azurerm_user_assigned_identity.this["app"].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "app_foundry_user" {
  scope              = azurerm_cognitive_account.foundry.id
  role_definition_id = local.role_definition_ids.foundry_user
  principal_id       = azurerm_user_assigned_identity.this["app"].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "app_foundry_user_images" {
  count = var.enable_image_generation ? 1 : 0

  scope              = azurerm_cognitive_account.images[0].id
  role_definition_id = local.role_definition_ids.foundry_user
  principal_id       = azurerm_user_assigned_identity.this["app"].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_cosmosdb_sql_role_assignment" "app_data_contributor" {
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/${local.cosmos_data_contributor_role}"
  principal_id        = azurerm_user_assigned_identity.this["app"].principal_id
  scope               = "${azurerm_cosmosdb_account.this.id}/dbs/${azurerm_cosmosdb_sql_database.this.name}"
}

# ---------------------------------------------------------------------------
# Portfolio build (GitHub OIDC): read only the published containers and the
# 'published' blob container. Nothing else.
# ---------------------------------------------------------------------------

resource "azurerm_cosmosdb_sql_role_assignment" "portfolio_reader" {
  for_each = toset(local.portfolio_readable_containers)

  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/${local.cosmos_data_reader_role}"
  principal_id        = azurerm_user_assigned_identity.this["portfolio_build"].principal_id
  scope               = "${azurerm_cosmosdb_account.this.id}/dbs/${azurerm_cosmosdb_sql_database.this.name}/colls/${azurerm_cosmosdb_sql_container.this[each.value].name}"
}

resource "azurerm_role_assignment" "portfolio_published_reader" {
  scope              = "${azurerm_storage_account.this.id}/blobServices/default/containers/${azurerm_storage_container.this["published"].name}"
  role_definition_id = local.role_definition_ids.storage_blob_data_reader
  principal_id       = azurerm_user_assigned_identity.this["portfolio_build"].principal_id
  principal_type     = "ServicePrincipal"
}

# ---------------------------------------------------------------------------
# CI deploy (deploy.yml): build in ACR, then update the image of this one app.
# AcrPush alone cannot queue `az acr build` (scheduleRun/action and
# listBuildSourceUploadUrl/action), hence Container Registry Tasks Contributor.
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "ci_deploy_acr_push" {
  scope              = azurerm_container_registry.this.id
  role_definition_id = local.role_definition_ids.acr_push
  principal_id       = azurerm_user_assigned_identity.this["ci_deploy"].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "ci_deploy_acr_tasks" {
  scope              = azurerm_container_registry.this.id
  role_definition_id = local.role_definition_ids.acr_tasks_contributor
  principal_id       = azurerm_user_assigned_identity.this["ci_deploy"].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "ci_deploy_container_app" {
  scope              = azurerm_container_app.app.id
  role_definition_id = local.role_definition_ids.container_apps_contributor
  principal_id       = azurerm_user_assigned_identity.this["ci_deploy"].principal_id
  principal_type     = "ServicePrincipal"
}

# ---------------------------------------------------------------------------
# CI infra (infra.yml): Contributor on this resource group only. Contributor
# cannot write role assignments or locks: plans work, but applies that change
# role assignments need ci_infra_rbac_admin_enabled (below) or a local apply
# by the owner. State access is granted in the bootstrap root.
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "ci_infra_contributor" {
  scope              = azurerm_resource_group.this.id
  role_definition_id = local.role_definition_ids.contributor
  principal_id       = azurerm_user_assigned_identity.this["ci_infra"].principal_id
  principal_type     = "ServicePrincipal"
}

locals {
  # Roles ci_infra may assign/remove when the constrained RBAC admin role is
  # enabled: exactly the roles this configuration assigns, minus Contributor
  # and RBAC Administrator (ci_infra's own roles stay owner-managed).
  ci_infra_assignable_role_ids = [
    local.role_ids.acr_pull,
    local.role_ids.acr_push,
    local.role_ids.acr_tasks_contributor,
    local.role_ids.container_apps_contributor,
    local.role_ids.foundry_user,
    local.role_ids.key_vault_secrets_officer,
    local.role_ids.key_vault_secrets_user,
    local.role_ids.storage_blob_data_contributor,
    local.role_ids.storage_blob_data_reader,
  ]

  # Principals it may assign them to: the other identities and the owner. Not
  # ci_infra itself, so it cannot widen its own access.
  ci_infra_assignable_principal_ids = concat(
    [for key, identity in azurerm_user_assigned_identity.this : identity.principal_id if key != "ci_infra"],
    [var.owner_object_id],
  )

  ci_infra_rbac_condition = <<-EOT
    (
     (
      !(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})
     )
     OR
     (
      @Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${join(", ", local.ci_infra_assignable_role_ids)}}
      AND
      @Request[Microsoft.Authorization/roleAssignments:PrincipalId] ForAnyOfAnyValues:GuidEquals {${join(", ", local.ci_infra_assignable_principal_ids)}}
     )
    )
    AND
    (
     (
      !(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})
     )
     OR
     (
      @Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${join(", ", local.ci_infra_assignable_role_ids)}}
      AND
      @Resource[Microsoft.Authorization/roleAssignments:PrincipalId] ForAnyOfAnyValues:GuidEquals {${join(", ", local.ci_infra_assignable_principal_ids)}}
     )
    )
  EOT
}

resource "azurerm_role_assignment" "ci_infra_rbac_admin" {
  count = var.ci_infra_rbac_admin_enabled ? 1 : 0

  scope              = azurerm_resource_group.this.id
  role_definition_id = local.role_definition_ids.rbac_administrator
  principal_id       = azurerm_user_assigned_identity.this["ci_infra"].principal_id
  principal_type     = "ServicePrincipal"
  condition          = local.ci_infra_rbac_condition
  condition_version  = "2.0"
  description        = "Blog Writer v2 infra.yml: may only assign this configuration's roles to its own identities and the owner"
}

# ---------------------------------------------------------------------------
# Owner
# ---------------------------------------------------------------------------

# Needed to set the secret values (the vault is in RBAC mode; creating it does
# not grant data-plane access).
resource "azurerm_role_assignment" "owner_key_vault_secrets_officer" {
  scope              = azurerm_key_vault.this.id
  role_definition_id = local.role_definition_ids.key_vault_secrets_officer
  principal_id       = var.owner_object_id
  principal_type     = "User"
}

resource "azurerm_role_assignment" "owner_blob_data_contributor" {
  count = var.owner_data_access_enabled ? 1 : 0

  scope              = azurerm_storage_account.this.id
  role_definition_id = local.role_definition_ids.storage_blob_data_contributor
  principal_id       = var.owner_object_id
  principal_type     = "User"
}

resource "azurerm_cosmosdb_sql_role_assignment" "owner_data_contributor" {
  count = var.owner_data_access_enabled ? 1 : 0

  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/${local.cosmos_data_contributor_role}"
  principal_id        = var.owner_object_id
  scope               = "${azurerm_cosmosdb_account.this.id}/dbs/${azurerm_cosmosdb_sql_database.this.name}"
}
