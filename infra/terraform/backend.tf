# Remote state backend configuration for Azure Storage

terraform {
  backend "azurerm" {
    resource_group_name  = "rg-blog-writer-dev"
    storage_account_name = "blogwritertfstate"
    container_name       = "tfstate"
    key                  = "blog-writer.tfstate"
  }
}
