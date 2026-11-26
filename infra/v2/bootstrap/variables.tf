variable "subscription_id" {
  description = "Target subscription ID. Leave null to use ARM_SUBSCRIPTION_ID. Never commit a real value."
  type        = string
  default     = null
}

variable "location" {
  description = "Region for the state resource group and storage account. Must be allowed by the lab 'Allowed locations' policy."
  type        = string
  default     = "eastus"

  validation {
    condition = contains([
      "eastus", "eastus2", "westus", "westus2", "centralus", "northcentralus",
      "southcentralus", "westcentralus", "francecentral", "westeurope", "southindia",
    ], var.location)
    error_message = "location must be one of the regions allowed by the lab policy (East US is the default)."
  }
}

variable "resource_group_name" {
  description = "Resource group that holds only the Terraform state storage account."
  type        = string
  default     = "rg-blog-writer-v2-tfstate"
}

variable "storage_account_name" {
  description = "State storage account name (3-24 lowercase letters/digits, globally unique). Null derives 'stbw2tf<6-char hash>' from the subscription and resource group."
  type        = string
  default     = null

  validation {
    condition     = var.storage_account_name == null || can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3-24 lowercase letters or digits."
  }
}

variable "container_name" {
  description = "Blob container for state files."
  type        = string
  default     = "tfstate"
}

variable "state_key" {
  description = "Blob name the main infra/v2 root uses for its state (written into the generated backend config)."
  type        = string
  default     = "blog-writer-v2.tfstate"
}

variable "replication_type" {
  description = "Replication for the state account."
  type        = string
  default     = "GRS"

  validation {
    condition     = contains(["LRS", "ZRS", "GRS", "RAGRS", "GZRS", "RAGZRS"], var.replication_type)
    error_message = "replication_type must be one of LRS, ZRS, GRS, RAGRS, GZRS or RAGZRS."
  }
}

variable "soft_delete_retention_days" {
  description = "Blob and container soft-delete retention for the state account."
  type        = number
  default     = 30

  validation {
    condition     = var.soft_delete_retention_days >= 7 && var.soft_delete_retention_days <= 365
    error_message = "soft_delete_retention_days must be between 7 and 365."
  }
}

variable "state_contributor_principal_ids" {
  description = <<-EOT
    Extra principals (name => object/principal ID) that get Storage Blob Data
    Contributor on the tfstate container, e.g. { ci_infra = "<principal id>" }
    after the main root has created the ci_infra managed identity. The deployer
    running this root is always granted access.
  EOT
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for id in values(var.state_contributor_principal_ids) :
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))
    ])
    error_message = "Every state_contributor_principal_ids value must be a GUID."
  }
}

variable "enable_lock" {
  description = "Put a CanNotDelete lock on the state storage account."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Lab-required resource group tags (a deny policy blocks resource groups
# without them). Values live in terraform.tfvars, which is gitignored.
# ---------------------------------------------------------------------------

variable "tag_purpose" {
  description = "Value of the lab-required 'Purpose' tag."
  type        = string

  validation {
    condition     = length(trimspace(var.tag_purpose)) > 0
    error_message = "tag_purpose must not be empty."
  }
}

variable "tag_owner" {
  description = "Value of the lab-required 'Owner' tag."
  type        = string

  validation {
    condition     = length(trimspace(var.tag_owner)) > 0
    error_message = "tag_owner must not be empty."
  }
}

variable "tag_expiry_date" {
  description = "Value of the lab-required 'Expiry Date' tag, formatted YYYY-MM-DD."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.tag_expiry_date)) && can(formatdate("YYYY-MM-DD", "${var.tag_expiry_date}T00:00:00Z"))
    error_message = "tag_expiry_date must be a valid date formatted YYYY-MM-DD."
  }
}

variable "extra_tags" {
  description = "Additional tags. The three lab-required tags always win over keys given here."
  type        = map(string)
  default     = {}
}
