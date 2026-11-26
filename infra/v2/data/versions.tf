# Blog Writer v2: data root. Owns ONLY the v2 Cosmos DB account, its database,
# containers, the owner's data-plane role and a delete lock, inside the EXISTING
# v1 resource group (owner decision 2026-11-26: reuse the existing app, its
# custom domains and resource group; add only a new database for now).
#
# Kept separate from the platform root (infra/v2) on purpose: the data has its
# own lifecycle, and a platform destroy can never take the database with it.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
  }

  # Partial configuration: see backend.hcl.example. State lives in the existing
  # v1 state account under its own key, so no new resources are needed for it.
  backend "azurerm" {}
}

provider "azurerm" {
  # null => taken from ARM_SUBSCRIPTION_ID. Never commit a real subscription ID.
  subscription_id = var.subscription_id

  # Lab subscription: resource providers are registered out of band.
  resource_provider_registrations = "none"

  features {}
}
