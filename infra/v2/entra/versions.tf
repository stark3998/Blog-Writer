# Entra side of Easy Auth for Blog Writer v2. Applied by the owner (a tenant admin) from a
# workstation: it needs Microsoft Graph rights the CI identities deliberately do not have.
# Everything else (infra/v2 and infra/v2/data) is planned and applied by infra.yml.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.10"
    }
    # Read-only data sources: the tenant, the environment's default domain and the easyauth
    # managed identity that the platform root creates.
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
  }

  # Partial configuration: see backend.hcl.example (own state key in the existing account).
  backend "azurerm" {}
}

provider "azurerm" {
  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"
  features {}
}

provider "azuread" {}
