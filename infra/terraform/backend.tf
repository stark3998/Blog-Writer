# Remote state backend configuration for Azure Storage
# Prerequisites: Create a storage account and container for state:
#   az group create -n rg-terraform-state -l eastus
#   az storage account create -n blogwritertfstate -g rg-terraform-state -l eastus --sku Standard_LRS
#   az storage container create -n tfstate --account-name blogwritertfstate
#
# Then uncomment the backend block below and run: terraform init -migrate-state

# terraform {
#   backend "azurerm" {
#     resource_group_name  = "rg-terraform-state"
#     storage_account_name = "blogwritertfstate"
#     container_name       = "tfstate"
#     key                  = "blog-writer.tfstate"
#   }
# }
