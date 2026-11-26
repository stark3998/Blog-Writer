# Microsoft Foundry (Azure AI Services) in East US, Entra ID only.
# Lab policy enforces disableLocalAuth on AI Services (modify/DINE); it is set
# explicitly here so Terraform and the policy agree.

resource "azurerm_cognitive_account" "foundry" {
  name                  = local.names.foundry
  location              = azurerm_resource_group.this.location
  resource_group_name   = azurerm_resource_group.this.name
  kind                  = "AIServices"
  sku_name              = "S0"
  custom_subdomain_name = local.names.foundry # required for Entra ID tokens

  local_auth_enabled            = false
  public_network_access_enabled = true

  # Projects let the owner use the Foundry portal (Claude Marketplace
  # deployment, playground) against this account.
  project_management_enabled = var.foundry_project_enabled

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

resource "azurerm_cognitive_account_project" "this" {
  count = var.foundry_project_enabled ? 1 : 0

  name                 = local.names.foundry_project
  cognitive_account_id = azurerm_cognitive_account.foundry.id
  location             = azurerm_resource_group.this.location
  display_name         = "Blog Writer v2"
  description          = "Blog Writer v2 models and Claude deployment"

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

# GPT-6.2 Sol/Astra, GPT-6 Luna, text-embedding-3-large (+ optional
# gpt-5.6-sol). azurerm serialises deployment operations per account.
resource "azurerm_cognitive_deployment" "models" {
  for_each = local.foundry_deployments

  name                 = each.key
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  # Stay on the pinned version until it is retired; bump versions on purpose
  # (model_version_overrides) so served_by records stay meaningful.
  version_upgrade_option = "OnceCurrentVersionExpired"

  model {
    format  = "OpenAI"
    name    = each.key
    version = each.value.version
  }

  sku {
    name     = "GlobalStandard"
    capacity = each.value.capacity
  }
}

# OPT-IN and UNVERIFIED (default off). Claude is sold through the Azure
# Marketplace: the first deployment must be created in the Foundry portal so
# the owner can accept the offer terms. The model entry was verified with
# `az cognitiveservices model list` (format "Anthropic", version "2" = Hosted
# on Azure, GlobalStandard); whether an ARM-only create succeeds after the
# offer is accepted is not verified. azurerm_cognitive_deployment accepts any
# model format, so azapi is not needed.
resource "azurerm_cognitive_deployment" "claude" {
  count = var.enable_claude_deployment ? 1 : 0

  name                   = var.claude_deployment_name
  cognitive_account_id   = azurerm_cognitive_account.foundry.id
  version_upgrade_option = "OnceCurrentVersionExpired"

  model {
    format  = "Anthropic"
    name    = "claude-opus-5-5"
    version = var.claude_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.claude_capacity
  }

  depends_on = [azurerm_cognitive_deployment.models]
}

# ---------------------------------------------------------------------------
# Optional image account: gpt-image-2 exists only in East US 2.
# ---------------------------------------------------------------------------

resource "azurerm_cognitive_account" "images" {
  count = var.enable_image_generation ? 1 : 0

  name                  = local.names.foundry_images
  location              = var.image_location
  resource_group_name   = azurerm_resource_group.this.name
  kind                  = "AIServices"
  sku_name              = "S0"
  custom_subdomain_name = local.names.foundry_images

  local_auth_enabled            = false
  public_network_access_enabled = true

  tags = local.tags
}

resource "azurerm_cognitive_deployment" "image" {
  count = var.enable_image_generation ? 1 : 0

  name                   = "gpt-image-2"
  cognitive_account_id   = azurerm_cognitive_account.images[0].id
  version_upgrade_option = "OnceCurrentVersionExpired"

  model {
    format  = "OpenAI"
    name    = "gpt-image-2"
    version = var.image_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.image_model_capacity
  }
}
