# Invariant checks. They run on every plan and apply and report warnings
# (they never block). infra.yml should fail the job when an INVARIANT check
# fails, i.e. any check except the two reminders at the end of this file
# (health_probes_mode, lab_expiry_tag), which fail by design for a while.
# Read the statuses from `terraform show -json` (see README, CI step 5); a
# grep for "Check block assertion failed" would also trip on the reminders.

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

  # Planned configuration: auth required, exactly one excluded path.
  assert {
    condition = (
      azapi_resource.easy_auth.body.properties.platform.enabled == true &&
      azapi_resource.easy_auth.body.properties.globalValidation.unauthenticatedClientAction == "RedirectToLoginPage" &&
      jsonencode(azapi_resource.easy_auth.body.properties.globalValidation.excludedPaths) == jsonencode(["/api/health"]) &&
      azapi_resource.easy_auth.body.properties.login.tokenStore.enabled == false
    )
    error_message = "Easy Auth config must require authentication (RedirectToLoginPage), exclude exactly [\"/api/health\"] and keep the token store off."
  }

  # Deployed state (missing fields count as a failure).
  assert {
    condition = try(
      data.azapi_resource.live_auth_config.output.platform_enabled == true &&
      data.azapi_resource.live_auth_config.output.unauth_action == "RedirectToLoginPage" &&
      jsonencode(data.azapi_resource.live_auth_config.output.excluded_paths) == jsonencode(["/api/health"]) &&
      data.azapi_resource.live_auth_config.output.token_store_enabled != true &&
      jsonencode([for id in data.azapi_resource.live_auth_config.output.allowed_identities : lower(id)]) == jsonencode([lower(var.owner_object_id)]),
      false
    )
    error_message = "Deployed Easy Auth drifted: it must be enabled, redirect unauthenticated requests, exclude exactly /api/health, keep the token store off and allow only owner_object_id."
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
  assert {
    condition = (
      length(azurerm_container_app_environment.this.workload_profile) == 1 &&
      alltrue([for p in azurerm_container_app_environment.this.workload_profile : p.workload_profile_type == "Consumption"]) &&
      try(length(azurerm_container_app_environment.this.infrastructure_subnet_id), 0) == 0
    )
    error_message = "The Container Apps environment must have only the Consumption workload profile and no VNet (dedicated profiles, premium ingress and private endpoints cost >= $73/month)."
  }
}

check "acr_admin_disabled" {
  assert {
    condition     = azurerm_container_registry.this.admin_enabled == false
    error_message = "ACR admin user must stay disabled (pulls use the app identity, pushes use ci_deploy)."
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
  assert {
    condition     = azurerm_cosmosdb_account.this.local_authentication_enabled == false
    error_message = "Cosmos DB local (key) authentication must stay disabled."
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

check "published_blogs_contract" {
  assert {
    condition = (
      azurerm_cosmosdb_sql_container.this["published-blogs"].partition_key_paths == tolist(["/id"]) &&
      azurerm_cosmosdb_sql_container.this["published-blogs-staging"].partition_key_paths == tolist(["/id"])
    )
    error_message = "published-blogs(-staging) must keep partition key /id: the portfolio point-reads item(slug, slug) and relies on id == slug."
  }
}

# ---------------------------------------------------------------------------
# Reminders (not invariants): they fail by design until acted on, so CI must
# not gate on them.
# ---------------------------------------------------------------------------

check "health_probes_mode" {
  assert {
    condition     = var.http_health_probes_enabled
    error_message = "Readiness/startup probes are still TCP (placeholder mode). After deploy.yml has rolled out an image that serves /api/health, set http_health_probes_enabled = true."
  }
}

check "lab_expiry_tag" {
  assert {
    condition     = timecmp(plantimestamp(), timeadd("${var.tag_expiry_date}T00:00:00Z", "-720h")) < 0
    error_message = "The lab 'Expiry Date' tag (${var.tag_expiry_date}) passes within 30 days or already passed. Ask the lab admins about extending it, then update tag_expiry_date."
  }
}
