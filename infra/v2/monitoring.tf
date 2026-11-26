# Out-of-band alerting target and an advisory budget. Log-based alert rules
# (no scheduler_tick in 15 min, restarts/OOM, job_failed, ...) are added once
# the app's structured log schema exists; they will target this action group.

resource "azurerm_monitor_action_group" "owner" {
  name                = local.names.action_group
  resource_group_name = azurerm_resource_group.this.name
  short_name          = "bw2-owner" # max 12 characters
  tags                = local.tags

  email_receiver {
    name                    = "owner-email"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }
}

# Budgets must start on the first day of a month that is not in the past.
# time_static records the first apply, so the start date never drifts (a
# changed start date would force a new budget).
resource "time_static" "budget_start" {}

resource "azurerm_consumption_budget_resource_group" "advisory" {
  name              = local.names.budget
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.monthly_budget_amount
  time_grain        = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00'Z'", time_static.budget_start.rfc3339)
  }

  notification {
    enabled        = true
    threshold      = 50
    threshold_type = "Actual"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = [var.alert_email]
    contact_groups = [azurerm_monitor_action_group.owner.id]
  }

  notification {
    enabled        = true
    threshold      = 90
    threshold_type = "Actual"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = [var.alert_email]
    contact_groups = [azurerm_monitor_action_group.owner.id]
  }

  notification {
    enabled        = true
    threshold      = 100
    threshold_type = "Forecasted"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = [var.alert_email]
    contact_groups = [azurerm_monitor_action_group.owner.id]
  }
}
