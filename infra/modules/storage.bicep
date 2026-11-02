// App storage (documents, processed) and a dedicated agent storage account (BYO for Foundry agents).
// Shared-key access is disabled. Access is by managed identity only.
param location string
param suffix string
param tags object
param isPrivate bool
param uamiPrincipalId string
param deployerPrincipalId string = ''

var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var publicAccess = isPrivate ? 'Disabled' : 'Enabled'
var networkDefault = isPrivate ? 'Deny' : 'Allow'

resource appAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('stidp${suffix}', 24)
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: publicAccess
    networkAcls: {
      defaultAction: networkDefault
      bypass: 'AzureServices'
    }
  }
}

resource appBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: appAccount
  name: 'default'
}

resource documentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: appBlobService
  name: 'documents'
  properties: {
    publicAccess: 'None'
  }
}

resource processedContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: appBlobService
  name: 'processed'
  properties: {
    publicAccess: 'None'
  }
}

resource agentAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('stagt${suffix}', 24)
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: publicAccess
    networkAcls: {
      defaultAction: networkDefault
      bypass: 'AzureServices'
    }
  }
}

resource appBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appAccount.id, uamiPrincipalId, storageBlobDataContributor)
  scope: appAccount
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
  }
}

resource deployerBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(appAccount.id, deployerPrincipalId, storageBlobDataContributor)
  scope: appAccount
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
  }
}

output appStorageId string = appAccount.id
output appStorageName string = appAccount.name
output appBlobEndpoint string = appAccount.properties.primaryEndpoints.blob
output agentStorageId string = agentAccount.id
output agentStorageName string = agentAccount.name
output agentBlobEndpoint string = agentAccount.properties.primaryEndpoints.blob
