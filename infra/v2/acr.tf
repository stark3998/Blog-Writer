# v2's own registry. The v1 registry is shared with another app and is never
# referenced.

resource "azurerm_container_registry" "this" {
  name                = local.names.registry
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"

  # Pulls use the app identity (AcrPull); pushes and builds use ci_deploy.
  admin_enabled = false

  # AcrPull/AcrPush are only honoured in this mode (the ABAC mode needs the
  # "Container Registry Repository *" roles instead).
  role_assignment_mode = "LegacyRegistryPermissions"

  tags = local.tags
}

# Scheduled purge: deletes tags older than acr_purge_older_than (always keeping
# the newest acr_purge_keep) and untagged manifests in the app repository.
# azurerm_container_registry_task supports encoded (inline YAML) steps and
# timer triggers, so no azapi is needed. Runs as an ACR task in the registry's
# own context (no identity or credentials).
resource "azurerm_container_registry_task" "purge" {
  count = var.acr_purge_enabled ? 1 : 0

  name                  = "purge-old-images"
  container_registry_id = azurerm_container_registry.this.id
  timeout_in_seconds    = 3600

  platform {
    os = "Linux"
  }

  encoded_step {
    task_content = base64encode(<<-YAML
      version: v1.1.0
      steps:
        - cmd: acr purge --filter '${var.container_image_repository}:.*' --ago ${var.acr_purge_older_than} --keep ${var.acr_purge_keep} --untagged
          disableWorkingDirectoryOverride: true
          timeout: 3600
    YAML
    )
  }

  timer_trigger {
    name     = "daily"
    schedule = var.acr_purge_schedule
    enabled  = true
  }

  tags = local.tags
}
