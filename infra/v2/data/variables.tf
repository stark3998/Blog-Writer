variable "subscription_id" {
  description = "Target subscription. Null uses ARM_SUBSCRIPTION_ID (preferred: keep real IDs out of the public repo)."
  type        = string
  default     = null
}

variable "resource_group_name" {
  description = "EXISTING resource group that hosts the v1 app (and its custom domains). This root never creates or changes it."
  type        = string
  default     = "rg-blog-writer-dev"
}

variable "name_prefix" {
  description = "Prefix for the Cosmos DB account name (lowercase letters, digits, single hyphens)."
  type        = string
  default     = "blogwriter-v2"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}[a-z0-9]$", var.name_prefix)) && !strcontains(var.name_prefix, "--")
    error_message = "name_prefix must be 4-26 chars of lowercase letters, digits and single hyphens, starting with a letter."
  }
}

variable "name_suffix" {
  description = "Suffix for the globally unique account name. Null derives a stable 6-character hash of subscription + resource group."
  type        = string
  default     = null

  validation {
    condition     = var.name_suffix == null || can(regex("^[a-z0-9]{4,6}$", var.name_suffix))
    error_message = "name_suffix must be 4-6 lowercase letters or digits."
  }
}

variable "owner_object_id" {
  description = "Entra object ID of the owner. Gets Cosmos DB Built-in Data Contributor on the database (migration, restore drills, debugging), because key auth is disabled."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.owner_object_id)) && var.owner_object_id != "00000000-0000-0000-0000-000000000000"
    error_message = "owner_object_id must be the owner's real Entra object ID (a GUID, not the all-zero placeholder)."
  }
}

variable "enable_lock" {
  description = "CanNotDelete lock on the Cosmos DB account. Creating or removing it needs Owner or User Access Administrator."
  type        = bool
  default     = true
}

variable "extra_tags" {
  description = "Additional tags for the Cosmos DB account."
  type        = map(string)
  default     = {}
}
