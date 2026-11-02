// Project connections (AI Search, Cosmos DB, agent storage, App Insights), the Agents capability host,
// and the project identity's RBAC. The ordering follows the standard agent setup (role before and after the host).
// Adapted from Microsoft's foundry-samples (MIT). See infra/NOTICE.md.
param location string
param accountName string
param projectName string
param projectPrincipalId string
param agentHostEnabled bool
param appInsightsName string
param searchName string
param searchEndpoint string
param cosmosName string
param cosmosEndpoint string
param agentStorageName string
param agentStorageBlobEndpoint string

var cosmosOperator = '230815da-be43-4aae-9cb4-875f7bd000aa'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageBlobDataOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var cosmosDataContributor = '00000000-0000-0000-0000-000000000002'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosName
}

resource agentStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: agentStorageName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource connectionSearch 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'idp-search'
  properties: {
    category: 'CognitiveSearch'
    target: searchEndpoint
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: search.id
      location: location
    }
  }
}

resource connectionCosmos 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'idp-cosmos'
  properties: {
    category: 'CosmosDB'
    target: cosmosEndpoint
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: cosmos.id
      location: location
    }
  }
}

resource connectionStorage 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'idp-agent-storage'
  properties: {
    category: 'AzureStorageAccount'
    target: agentStorageBlobEndpoint
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: agentStorage.id
      location: location
    }
  }
}

resource connectionAppInsights 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'idp-appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    authType: 'ApiKey'
    credentials: {
      key: appInsights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
  }
}

resource projectCosmosOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (agentHostEnabled) {
  name: guid(cosmos.id, projectPrincipalId, cosmosOperator)
  scope: cosmos
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cosmosOperator)
  }
}

resource projectStorageContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (agentHostEnabled) {
  name: guid(agentStorage.id, projectPrincipalId, storageBlobDataContributor)
  scope: agentStorage
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
  }
}

resource projectSearchIndexRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (agentHostEnabled) {
  name: guid(search.id, projectPrincipalId, searchIndexDataContributor)
  scope: search
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
  }
}

resource projectSearchServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (agentHostEnabled) {
  name: guid(search.id, projectPrincipalId, searchServiceContributor)
  scope: search
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
  }
}

resource projectCapabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = if (agentHostEnabled) {
  parent: project
  name: 'idp-agents-caphost'
  properties: {
    capabilityHostKind: 'Agents'
    vectorStoreConnections: [
      connectionSearch.name
    ]
    storageConnections: [
      connectionStorage.name
    ]
    threadStorageConnections: [
      connectionCosmos.name
    ]
  }
  dependsOn: [
    projectCosmosOperator
    projectStorageContributor
    projectSearchIndexRole
    projectSearchServiceRole
  ]
}

resource projectStorageOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (agentHostEnabled) {
  name: guid(agentStorage.id, projectPrincipalId, storageBlobDataOwner)
  scope: agentStorage
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwner)
  }
  dependsOn: [
    projectCapabilityHost
  ]
}

resource projectCosmosDataRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (agentHostEnabled) {
  parent: cosmos
  name: guid(cosmos.id, projectPrincipalId, cosmosDataContributor, 'project')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    scope: cosmos.id
  }
  dependsOn: [
    projectCapabilityHost
  ]
}

output capabilityHostEnabled bool = agentHostEnabled
