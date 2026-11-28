# The container registry is SHARED with another app and is only read here
# (main.tf). Its settings, admin user included, are never changed.
#
# Scheduled purge of the v2 repositories only (Blog Writer and, with
# site_hosts, the portfolio): deletes tags older than acr_purge_older_than
# (always keeping the newest acr_purge_keep per repository) and untagged
# manifests. Each filter (local.acr_purge_filters) is an anchored regular
# expression for exactly one of those repositories, so no other repository can
# match. Runs as an ACR task in the registry's own context (no
# identity or credentials). Check it once with the dry run in the README.

resource "azurerm_container_registry_task" "purge" {
  count = var.acr_purge_enabled ? 1 : 0

  name                  = local.names.acr_purge_task
  container_registry_id = local.registry_id
  timeout_in_seconds    = 3600

  platform {
    os = "Linux"
  }

  encoded_step {
    task_content = base64encode(<<-YAML
      version: v1.1.0
      steps:
        - cmd: acr purge ${join(" ", [for f in local.acr_purge_filters : "--filter '${f}'"])} --ago ${var.acr_purge_older_than} --keep ${var.acr_purge_keep} --untagged
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
