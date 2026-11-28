output "cosmos_account_name" {
  description = "v2 Cosmos DB account (pass to the platform root as cosmos_account_name)."
  value       = azurerm_cosmosdb_account.this.name
}

output "cosmos_endpoint" {
  description = "v2 Cosmos DB endpoint (COSMOS_ENDPOINT for the app; Entra ID auth only)."
  value       = azurerm_cosmosdb_account.this.endpoint
}

output "cosmos_database_name" {
  description = "v2 database name."
  value       = azurerm_cosmosdb_sql_database.this.name
}

output "cosmos_containers" {
  description = "Container name => partition key."
  value       = { for name, c in local.containers : name => c.partition_key }
}
