# Microsoft Foundry (Azure AI Services) in East US, Entra ID only. New and
# managed here. Lab policy enforces disableLocalAuth on AI Services
# (modify/DINE); it is set explicitly so Terraform and the policy agree.

resource "azurerm_cognitive_account" "foundry" {
  name                  = local.names.foundry
  location              = var.location
  resource_group_name   = data.azurerm_resource_group.this.name
  kind                  = "AIServices"
  sku_name              = "S0"
  custom_subdomain_name = local.names.foundry # required for Entra ID tokens

  local_auth_enabled            = false
  public_network_access_enabled = true

  # Projects let the owner use the Foundry portal (playground, quota view)
  # against this account.
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
  location             = var.location
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

# Claude Opus 5.5, Hosted on Azure (format "Anthropic", version "2"),
# GlobalStandard. On by default (owner decision 2026-11-26: quota verified,
# 5,000 units free in East US). Claude is sold through the Azure Marketplace:
# if the create fails on the offer terms, accept them once in the Foundry
# portal and apply again (README). Never alongside a portal-created deployment
# of the same name on this account: import that one instead.
resource "azapi_resource" "claude" {
  count = var.enable_claude_deployment ? 1 : 0

  # azapi, not azurerm_cognitive_deployment: Anthropic deployments require
  # properties.modelProviderData (industry, organizationName, countryCode), which
  # the azurerm resource cannot send (400 InvalidModelProviderData).
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2026-03-01"
  name                      = var.claude_deployment_name
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.claude_capacity
    }
    properties = {
      model = {
        format  = "Anthropic"
        name    = "claude-opus-5-5"
        version = var.claude_model_version
      }
      versionUpgradeOption = "OnceCurrentVersionExpired"
      modelProviderData = {
        industry         = var.claude_provider_industry
        organizationName = var.claude_provider_organization
        countryCode      = var.claude_provider_country_code
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.claude_provider_organization != null && length(trimspace(coalesce(var.claude_provider_organization, ""))) > 0
      error_message = "Set claude_provider_organization (the organization name accepted with the Anthropic Marketplace terms) in terraform.tfvars."
    }
  }

  # Deployments on one account are created one at a time.
  depends_on = [azurerm_cognitive_deployment.models]
}

# ---------------------------------------------------------------------------
# Optional image account: gpt-image-2 exists only in East US 2.
# ---------------------------------------------------------------------------

resource "azurerm_cognitive_account" "images" {
  count = var.enable_image_generation ? 1 : 0

  name                  = local.names.foundry_images
  location              = var.image_location
  resource_group_name   = data.azurerm_resource_group.this.name
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
