// Cosmos DB (NoSQL, serverless, Entra-only) for the document library, reviews and jobs.
// Partition key /tenantId on every container.
param location string
param suffix string
param tags object
param isPrivate bool
param uamiPrincipalId string
param deployerPrincipalId string = ''

var cosmosDataContributor = '00000000-0000-0000-0000-000000000002'

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'cosmos-idp-${suffix}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
    publicNetworkAccess: isPrivate ? 'Disabled' : 'Enabled'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: 'idp'
  properties: {
    resource: {
      id: 'idp'
    }
  }
}

resource documentsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'documents'
  properties: {
    resource: {
      id: 'documents'
      partitionKey: {
        paths: [
          '/tenantId'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource reviewsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'reviews'
  properties: {
    resource: {
      id: 'reviews'
      partitionKey: {
        paths: [
          '/tenantId'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource jobsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'jobs'
  properties: {
    resource: {
      id: 'jobs'
      partitionKey: {
        paths: [
          '/tenantId'
        ]
        kind: 'Hash'
      }
      defaultTtl: 604800
    }
  }
}

resource uamiDataRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, uamiPrincipalId, cosmosDataContributor)
  properties: {
    principalId: uamiPrincipalId
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    scope: cosmosAccount.id
  }
}

resource deployerDataRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(deployerPrincipalId)) {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, deployerPrincipalId, cosmosDataContributor)
  properties: {
    principalId: deployerPrincipalId
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    scope: cosmosAccount.id
  }
}

output cosmosId string = cosmosAccount.id
output cosmosName string = cosmosAccount.name
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint
