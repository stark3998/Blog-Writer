# Web IQ account (grounding for research). azurerm has no Web IQ resource, so
# azapi is the only option. OPT-IN (default off):
#   * Microsoft.WebIQ was NotRegistered in the lab subscription on 2026-11-25;
#     the owner registers it (az provider register -n Microsoft.WebIQ).
#   * azapi 2.14 does not embed the Microsoft.WebIQ preview schema, so body
#     validation is disabled. Type, api-version, kind, SKU and the 'global'
#     location come from the provider registration and the plan; the empty
#     properties object is an assumption to confirm on first apply.
#   * Managed-identity binding only (no x-apikey): after apply, the owner binds
#     the app identity's client ID (output identity_client_ids.app) in the Web
#     IQ Developer Portal.

resource "azapi_resource" "webiq" {
  count = var.enable_webiq ? 1 : 0

  type      = "Microsoft.WebIQ/accounts@2026-06-01-preview"
  name      = local.names.webiq
  parent_id = azurerm_resource_group.this.id
  location  = "global" # global resources are exempt from 'Allowed locations'
  tags      = local.tags

  schema_validation_enabled = false

  body = {
    kind = "WebIQ.Grounding"
    sku = {
      name = var.webiq_sku
    }
    properties = {}
  }

  response_export_values = ["properties"]
}
