# Bootstrap root for Blog Writer v2 Terraform state.
#
# Uses LOCAL state on purpose: it creates the storage account that the main
# root (infra/v2) uses as its azurerm backend. Keep the local terraform.tfstate
# safe (it is gitignored); losing it only means re-importing the resource
# group, storage account, container, role assignments and lock.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
  }
}

provider "azurerm" {
  # null => taken from ARM_SUBSCRIPTION_ID. Never commit a real subscription ID.
  subscription_id = var.subscription_id

  # Shared-key access is disabled on the state account, so any data-plane call
  # the provider makes has to use Entra ID.
  storage_use_azuread = true

  # Lab subscription: resource providers are registered out of band.
  resource_provider_registrations = "none"

  features {
    resource_group {
      prevent_deletion_if_contains_resources = true
    }

    storage {
      # Skip the queue/static-website data-plane reads; they are not used here.
      data_plane_available = false
    }
  }
}
