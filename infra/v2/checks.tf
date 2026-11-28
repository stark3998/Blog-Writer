# Invariant checks. They run on every plan and apply and report warnings
# (they never block). infra.yml should fail the job when an INVARIANT check
# fails, i.e. any check except the reminders at the end of this file
# (health_probes_mode, scheduler_tick_alert, lab_expiry_tag), which fail by
# design for a while. Read the statuses from `terraform show -json` (see
# README, CI); a grep for "Check block assertion failed" would also trip on
# the reminders.

check "easy_auth" {
  # Live read of the deployed auth config, so portal changes are caught too.
  # Deferred to apply time when the app does not exist yet.
  data "azapi_resource" "live_auth_config" {
    type        = "Microsoft.App/containerApps/authConfigs@2026-07-01"
    resource_id = azapi_resource.easy_auth.id

    response_export_values = {
      platform_enabled    = "properties.platform.enabled"
      unauth_action       = "properties.globalValidation.unauthenticatedClientAction"
      excluded_paths      = "properties.globalValidation.excludedPaths"
      token_store_enabled = "properties.login.tokenStore.enabled"
      allowed_identities  = "properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.identities"
    }
  }

  # Planned configuration: auth required (or, with a public site, anonymous
  # requests passed to the app, which enforces owner-only access on every host
  # but the site hosts), exactly one excluded path, owner only.
  assert {
    condition = (
      azapi_resource.easy_auth.body.properties.platform.enabled == true &&
      azapi_resource.easy_auth.body.properties.globalValidation.unauthenticatedClientAction == local.easyauth_unauthenticated_action &&
      (local.easyauth_unauthenticated_action == "RedirectToLoginPage" || contains(keys(local.app_env), "BW_SITE_HOSTS")) &&
      jsonencode(azapi_resource.easy_auth.body.properties.globalValidation.excludedPaths) == jsonencode(["/api/health"]) &&
      azapi_resource.easy_auth.body.properties.login.tokenStore.enabled == false &&
      jsonencode(azapi_resource.easy_auth.body.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.identities) == jsonencode([var.owner_object_id])
    )
    error_message = "Easy Auth config must require authentication (RedirectToLoginPage; AllowAnonymous only with site_hosts, where the app gates every other host), exclude exactly [\"/api/health\"], keep the token store off and allow only owner_object_id."
  }

  # Deployed state (missing fields count as a failure).
  assert {
    condition = try(
      data.azapi_resource.live_auth_config.output.platform_enabled == true &&
      data.azapi_resource.live_auth_config.output.unauth_action == local.easyauth_unauthenticated_action &&
      jsonencode(data.azapi_resource.live_auth_config.output.excluded_paths) == jsonencode(["/api/health"]) &&
      data.azapi_resource.live_auth_config.output.token_store_enabled != true &&
      jsonencode([for id in data.azapi_resource.live_auth_config.output.allowed_identities : lower(id)]) == jsonencode([lower(var.owner_object_id)]),
      false
    )
    error_message = "Deployed Easy Auth drifted: it must be enabled, handle unauthenticated requests as planned (RedirectToLoginPage, or AllowAnonymous with site_hosts), exclude exactly /api/health, keep the token store off and allow only owner_object_id."
  }
}

check "single_replica" {
  assert {
    condition = (
      azurerm_container_app.app.revision_mode == "Single" &&
      azurerm_container_app.app.template[0].min_replicas == 1 &&
      azurerm_container_app.app.template[0].max_replicas == 1
    )
    error_message = "The container app must run a single revision with min = max = 1 replica."
  }
}

check "consumption_only_environment" {
  # Live read of the shared environment: a legacy Consumption-only environment
  # has no workloadProfiles at all; a workload-profiles one may only have the
  # Consumption profile. No VNet either.
  data "azapi_resource" "live_environment" {
    type        = "Microsoft.App/managedEnvironments@2024-03-01"
    resource_id = data.azurerm_container_app_environment.this.id

    response_export_values = {
      workload_profiles = "properties.workloadProfiles"
      vnet              = "properties.vnetConfiguration"
    }
  }

  assert {
    condition = (
      try(length(azurerm_container_app.app.workload_profile_name), 0) == 0 &&
      alltrue(try([for p in data.azapi_resource.live_environment.output.workload_profiles : p.workloadProfileType == "Consumption"], [true])) &&
      try(data.azapi_resource.live_environment.output.vnet, null) == null
    )
    error_message = "The app must run on Consumption only (no workload_profile_name) in an environment without dedicated profiles or a VNet (dedicated profiles, premium ingress and private endpoints cost >= $73/month)."
  }
}

check "app_fqdn" {
  # BW_PUBLIC_ORIGIN and the Entra redirect URIs are computed as
  # <app>.<environment default domain>; the real ingress FQDN must match.
  assert {
    condition     = azurerm_container_app.app.ingress[0].fqdn == local.app_default_fqdn
    error_message = "The app's ingress FQDN differs from <container_app_name>.<environment default domain>; set public_origin (and check the Entra redirect URIs)."
  }
}

check "storage_shared_key_disabled" {
  assert {
    condition = (
      azurerm_storage_account.this.shared_access_key_enabled == false &&
      azurerm_storage_account.this.allow_nested_items_to_be_public == false &&
      azurerm_storage_account.this.https_traffic_only_enabled == true
    )
    error_message = "Storage must be Entra-ID-only (shared key off), private (no public blob access) and HTTPS-only."
  }
}

check "cosmos_local_auth_disabled" {
  # The data root owns the account; the app relies on Entra ID only.
  assert {
    condition     = try(data.azapi_resource.cosmos_account.output.disable_local_auth == true, false)
    error_message = "Cosmos DB local (key) authentication must stay disabled on the v2 account (infra/v2/data)."
  }
}

check "foundry_local_auth_disabled" {
  assert {
    condition = (
      azurerm_cognitive_account.foundry.local_auth_enabled == false &&
      alltrue([for a in azurerm_cognitive_account.images : a.local_auth_enabled == false])
    )
    error_message = "Foundry (AI Services) local auth must stay disabled (also enforced by lab policy)."
  }
}

# ---------------------------------------------------------------------------
# Reminders (not invariants): they fail by design until acted on, so CI must
# not gate on them.
# ---------------------------------------------------------------------------

check "health_probes_mode" {
  assert {
    condition     = var.http_health_probes_enabled
    error_message = "Readiness/startup probes are TCP (placeholder mode). Once deploy has rolled out an image that serves /api/health, apply with http_health_probes_enabled = true (the default)."
  }
}

check "scheduler_tick_alert" {
  assert {
    condition     = var.scheduler_tick_alert_enabled
    error_message = "The 'no scheduler tick for 15 minutes' alert is disabled. Enable scheduler_tick_alert_enabled once the scheduler logs schedule_tick on every tick (today it only logs ticks that did something)."
  }
}

check "lab_expiry_tag" {
  assert {
    condition     = timecmp(plantimestamp(), timeadd("${var.tag_expiry_date}T00:00:00Z", "-720h")) < 0
    error_message = "The lab 'Expiry Date' tag (${var.tag_expiry_date}) passes within 30 days or already passed. Ask the lab admins about extending it, then update tag_expiry_date."
  }
}
