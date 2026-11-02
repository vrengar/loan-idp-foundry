// AI Search (Basic, Entra-only). In private mode it has a shared private link to the Foundry account
// for the integrated vectorizer. The link must be approved after deployment (scripts/approve-private-link.ps1).
// Adapted from Microsoft's foundry-samples (MIT). See infra/NOTICE.md.
param location string
param suffix string
param tags object
param isPrivate bool
param foundryAccountName string
param foundryAccountId string
param foundryAccountPrincipalId string
param uamiPrincipalId string
param deployerPrincipalId string = ''

var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var cognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: 'srch-idp-${suffix}'
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: isPrivate ? 'disabled' : 'enabled'
    disableLocalAuth: true
    semanticSearch: 'free'
  }
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

// AI Search rejects the PE group 'account' for shared private links; the integrated vectorizer needs 'openai_account'.
resource foundryLink 'Microsoft.Search/searchServices/sharedPrivateLinkResources@2024-06-01-preview' = if (isPrivate) {
  parent: search
  name: 'spl-foundry-openai-${suffix}'
  properties: {
    privateLinkResourceId: foundryAccountId
    groupId: 'openai_account'
    requestMessage: 'AI Search integrated vectorizer requires private access to the Foundry account (openai_account)'
  }
}

resource uamiIndexRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, uamiPrincipalId, searchIndexDataContributor)
  scope: search
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
  }
}

resource uamiServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, uamiPrincipalId, searchServiceContributor)
  scope: search
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
  }
}

resource foundryIndexRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, foundryAccountPrincipalId, searchIndexDataContributor)
  scope: search
  properties: {
    principalId: foundryAccountPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
  }
}

resource foundryServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, foundryAccountPrincipalId, searchServiceContributor)
  scope: search
  properties: {
    principalId: foundryAccountPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
  }
}

resource searchOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, search.id, cognitiveServicesOpenAiUser)
  scope: foundryAccount
  properties: {
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUser)
  }
}

resource deployerIndexRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(search.id, deployerPrincipalId, searchIndexDataContributor)
  scope: search
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
  }
}

resource deployerServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(search.id, deployerPrincipalId, searchServiceContributor)
  scope: search
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
  }
}

output searchId string = search.id
output searchName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
