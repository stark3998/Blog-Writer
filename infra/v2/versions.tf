terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.14"
    }
    # Entra app registration for Easy Auth (create_entra_app, default on).
    # Configured in every run (Terraform configures every provider a module
    # declares resources for).
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.10"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }

  # Partial configuration: see backend.hcl.example. State lives in the existing
  # state account (the one infra/v2/data uses) under its own key, so no new
  # resources are needed for it.
  backend "azurerm" {}
}

provider "azurerm" {
  # null => taken from ARM_SUBSCRIPTION_ID. Never commit a real subscription ID.
  subscription_id = var.subscription_id

  # Shared-key access is disabled on the v2 storage account, so any data-plane
  # call the provider makes has to use Entra ID.
  storage_use_azuread = true

  # Lab subscription: resource providers are registered out of band. The
  # ci_infra identity only has resource-group scope and cannot register them.
  resource_provider_registrations = "none"

  features {
    key_vault {
      # Purge protection is on, so a purge would fail anyway. Recover a
      # soft-deleted vault with the same name instead of failing the create.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }

    cognitive_account {
      purge_soft_delete_on_destroy = false
    }

    storage {
      # azurerm_storage_account would otherwise call the queue/static-website
      # data plane, which this configuration does not use.
      data_plane_available = false
    }
  }
}

provider "azapi" {
  subscription_id = var.subscription_id

  # Resource providers (Microsoft.WebIQ included) are registered by the owner.
  # Never let a plan or apply try to register them.
  skip_provider_registration = true
}

provider "azuread" {}
