# Web IQ account (grounding for research). azurerm has no Web IQ resource, so
# azapi is the only option. On by default (owner decision 2026-11-26);
# Microsoft.WebIQ is registered in the subscription.
#   * azapi 2.14 does not embed the Microsoft.WebIQ preview schema, so body
#     validation is disabled. Type, api-version and the 'global' location were
#     confirmed from the provider registration (2026-11-26); kind, SKU and the
#     empty properties object are assumptions to confirm on the first apply.
#   * The whole 'properties' object is exported. If it carries an https
#     endpoint under a known name (local.webiq_exported_endpoint), the app gets
#     it as BW_RESEARCH_WEBIQ_ENDPOINT; otherwise set var.webiq_endpoint.
#   * Managed-identity binding only (no x-apikey): after apply, the owner binds
#     the app identity's client ID (output identity_client_ids.app) in the Web
#     IQ Developer Portal.

resource "azapi_resource" "webiq" {
  count = var.enable_webiq ? 1 : 0

  type      = "Microsoft.WebIQ/accounts@2026-06-01-preview"
  name      = local.names.webiq
  parent_id = data.azurerm_resource_group.this.id
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

  lifecycle {
    # The Web IQ resource provider lower-cases the first letter of tag keys
    # ("Owner" -> "owner"), which would otherwise show a perpetual diff.
    ignore_changes = [tags]
  }
}
