# Out-of-band alerting: an action group that emails the owner, Azure Monitor
# log search alert rules on the EXISTING workspace, and an advisory budget.
#
# The legacy environment writes to the workspace through the 'log-analytics'
# destination: tables ContainerAppConsoleLogs_CL (Log_s = one raw stdout line,
# ContainerAppName_s, RevisionName_s) and ContainerAppSystemLogs_CL (Reason_s,
# Log_s, ...). Every query is filtered to the v2 app. App lines are the
# structured JSON of server/src/blogwriter/core/logging.py, parsed with
# parse_json(Log_s); only event names, ids and codes are projected, never the
# raw app line.
#
# Event rules (alert_event, job failures, restarts) select records by
# ingestion_time() over the last evaluation period plus one minute: a record
# that arrives late is still evaluated (ingestion latency measured on this
# workspace: p99 ~30 s, max ~60 s), and a late evaluation leaves no gap. An
# event ingested in the overlapping minute can be emailed twice. These rules
# are stateless (one email per evaluation with results).

locals {
  alert_frequency_minutes = 5
  alert_lookback          = "${local.alert_frequency_minutes + 1}m"
  alert_app               = var.container_app_name # validated: safe inside a KQL string literal

  # Job and scheduler failures that do not already raise an alert_event
  # (a quarantine does, through the quarantine hook in blogwriter.main).
  job_failure_events = [
    "job_execution_crashed",       # unexpected error in the runner itself
    "job_settle_failed",           # a finished job could not be recorded
    "job_quarantine_alert_failed", # the quarantine alert_event could not be raised
    "schedule_fire_crashed",       # a schedule item could not be fired
    "schedule_tick_crashed",       # the scheduler tick raised
  ]

  alert_rules = {
    alert_event = {
      display_name = "Blog Writer v2: alert_event"
      description  = "The app raised an out-of-band alert (blogwriter.notify.emit_alert): job_failed, publish_failed, needs_attention, quarantined, brake_tripped, notification_failed or test."
      severity     = 1
      enabled      = true
      stateful     = false
      frequency    = local.alert_frequency_minutes
      window       = 15
      query        = <<-KQL
        let app = "${local.alert_app}";
        ContainerAppConsoleLogs_CL
        | where ingestion_time() > ago(${local.alert_lookback})
        | where ContainerAppName_s == app
        | extend entry = parse_json(Log_s)
        | where tostring(entry.event) == "alert_event"
        | project TimeGenerated, RevisionName_s, alert_kind = tostring(entry.alert_kind), ref_id = tostring(entry.ref_id), reason = tostring(entry.reason)
      KQL
    }

    job_failures = {
      display_name = "Blog Writer v2: job failures"
      description  = "A job failed permanently (job_settled with outcome failed) or the runner/scheduler crashed. Quarantines arrive through the alert_event rule."
      severity     = 2
      enabled      = true
      stateful     = false
      frequency    = local.alert_frequency_minutes
      window       = 15
      query        = <<-KQL
        let app = "${local.alert_app}";
        ContainerAppConsoleLogs_CL
        | where ingestion_time() > ago(${local.alert_lookback})
        | where ContainerAppName_s == app
        | extend entry = parse_json(Log_s)
        | extend event = tostring(entry.event), outcome = tostring(entry.outcome)
        | where (event == "job_settled" and outcome == "failed") or event in (${join(", ", [for e in local.job_failure_events : "\"${e}\""])})
        | project TimeGenerated, RevisionName_s, event, outcome, job_id = tostring(entry.job_id), job_type = tostring(entry.job_type), schedule_id = tostring(entry.schedule_id), error_type = tostring(entry.error_type)
      KQL
    }

    restarts = {
      display_name = "Blog Writer v2: restarts, OOM kills and crash loops"
      description  = "Platform restarts (failed probes), container exits (non-zero exit codes, OOMKilled / 137), back-off crash loops, revision failures, and the app's own crash-loop guard (safe mode)."
      severity     = 1
      enabled      = true
      stateful     = false
      frequency    = local.alert_frequency_minutes
      window       = 15
      query        = <<-KQL
        let app = "${local.alert_app}";
        let platform = ContainerAppSystemLogs_CL
            | where ingestion_time() > ago(${local.alert_lookback})
            | where ContainerAppName_s == app
            | where Reason_s in ("ContainerBackOff", "ContainerCrashing", "ContainerTerminated", "RevisionFailure", "ContainerAppFailure")
                or (Reason_s == "StoppingContainer" and Log_s contains "will be restarted")
                or (Log_s contains "terminated with exit code" and not(Log_s contains "exit code '0'"))
                or Log_s contains "OOMKilled"
            | project TimeGenerated, RevisionName_s, signal = Reason_s, detail = substring(Log_s, 0, 200);
        let guard = ContainerAppConsoleLogs_CL
            | where ingestion_time() > ago(${local.alert_lookback})
            | where ContainerAppName_s == app
            | extend entry = parse_json(Log_s)
            | extend event = tostring(entry.event)
            | where event in ("jobs_safe_mode", "jobs_boot_log_unavailable")
            | project TimeGenerated, RevisionName_s, signal = event, detail = strcat("boots=", tostring(entry.boots));
        union platform, guard
      KQL
    }

    # State rule: a row only while no schedule_tick was logged for 15 minutes.
    # Stateful (one email when it fires, one when it resolves). Disabled by
    # default: the scheduler logs schedule_tick only for ticks that fired,
    # parked or failed something, so an idle scheduler looks silent. Enable it
    # (scheduler_tick_alert_enabled) once every tick logs schedule_tick.
    scheduler_silent = {
      display_name = "Blog Writer v2: no scheduler tick for 15 minutes"
      description  = "No schedule_tick event from the v2 app in the last 15 minutes (the scheduler ticks every 60 s)."
      severity     = 2
      enabled      = var.scheduler_tick_alert_enabled
      stateful     = true
      frequency    = local.alert_frequency_minutes
      window       = 15
      query        = <<-KQL
        let app = "${local.alert_app}";
        ContainerAppConsoleLogs_CL
        | where TimeGenerated > ago(15m)
        | where ContainerAppName_s == app
        | extend entry = parse_json(Log_s)
        | where tostring(entry.event) == "schedule_tick"
        | summarize ticks = count()
        | where ticks == 0
      KQL
    }
  }
}

resource "azurerm_monitor_action_group" "owner" {
  name                = local.names.action_group
  resource_group_name = data.azurerm_resource_group.this.name
  short_name          = "bw2-owner" # max 12 characters
  tags                = local.tags

  email_receiver {
    name                    = "owner-email"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "app" {
  for_each = local.alert_rules

  name                 = "${local.names.alert_prefix}-${replace(each.key, "_", "-")}"
  display_name         = each.value.display_name
  description          = each.value.description
  resource_group_name  = data.azurerm_resource_group.this.name
  location             = local.log_analytics_location
  scopes               = [local.log_analytics_id]
  severity             = each.value.severity
  enabled              = each.value.enabled
  evaluation_frequency = "PT${each.value.frequency}M"
  window_duration      = "PT${each.value.window}M"

  # Stateless rules notify on every evaluation with results; stateful rules
  # notify once when they fire and once when they resolve.
  auto_mitigation_enabled = each.value.stateful

  criteria {
    query                   = each.value.query
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.owner.id]
    email_subject = each.value.display_name
    custom_properties = {
      app = var.container_app_name
    }
  }

  tags = local.tags
}

# Budgets must start on the first day of a month that is not in the past.
# time_static records the first apply, so the start date never drifts (a
# changed start date would force a new budget).
resource "time_static" "budget_start" {
  count = var.enable_budget ? 1 : 0
}

# Advisory only (budgets never stop resources). The resource group is shared,
# so this covers v1, v2 and everything else in it. The Claude Marketplace
# invoice is billed separately.
resource "azurerm_consumption_budget_resource_group" "advisory" {
  count = var.enable_budget ? 1 : 0

  name              = local.names.budget
  resource_group_id = data.azurerm_resource_group.this.id
  amount            = var.monthly_budget_amount
  time_grain        = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00'Z'", time_static.budget_start[0].rfc3339)
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
