// Loan IDP on Microsoft Foundry: platform infrastructure (P1).
// Standard agent setup with network injection, Cosmos DB, AI Search and Claude on Foundry.
// Patterns adapted from Microsoft's foundry-samples and Azure-Samples/claude (MIT). See infra/NOTICE.md.
targetScope = 'subscription'

@description('prod = private network (full isolation). dev = public endpoints.')
@allowed([ 'prod', 'dev' ])
param environmentName string = 'prod'

@description('Azure region. eastus hosts Claude (Global and Data Zone Standard) and the OpenAI models used here.')
param location string = 'eastus'

@description('Region for the Cosmos DB account. This subscription cannot create Cosmos accounts in eastus. Cosmos is reached through a private endpoint in the eastus VNet.')
param cosmosLocation string = 'eastus2'

@description('Resource group for this environment. The existing dev-space group is not touched.')
param resourceGroupName string = 'rg-idp-prod'

@description('private = Private Endpoints and agent network injection. public = public endpoints.')
@allowed([ 'private', 'public' ])
param networkMode string = 'private'

@description('Globally unique Foundry resource name (lowercase letters, digits, hyphens).')
param foundryAccountName string = 'idp-workspace-private'

@description('Foundry project inside the resource.')
param foundryProjectName string = 'idp-demo'

@description('Optional Entra object ID of a person who needs data-plane access for testing. Leave empty for none.')
param deployerPrincipalId string = ''

@description('Anthropic attestation: legal organization name (sent as modelProviderData).')
param claudeOrganizationName string

@description('Anthropic attestation: ISO 3166-1 alpha-2 country code.')
param claudeCountryCode string = 'US'

@description('Anthropic attestation: industry. Must be lowercase.')
@allowed([ 'technology', 'finance', 'healthcare', 'education', 'retail', 'manufacturing', 'government', 'media', 'other' ])
param claudeIndustry string = 'technology'

@description('Capacity in thousands of tokens per minute (K TPM) per deployment.')
param claudeSonnetCapacity int = 40
param claudeOpusCapacity int = 20
param claudeHaikuCapacity int = 20
param cuCompletionCapacity int = 50
param embeddingCapacity int = 120

@description('Deploy the project capability host so Foundry-managed agents can run later. Requires private mode.')
param deployAgentCapabilityHost bool = true

@description('Placeholder image until the app image is built (P2/P6).')
param apiImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var isPrivate = networkMode == 'private'
var agentHostEnabled = isPrivate && deployAgentCapabilityHost
var tags = {
  app: 'idp-demo'
  costcenter: 'loan-ops'
  env: environmentName
}
var suffix = uniqueString(subscription().subscriptionId, resourceGroupName, environmentName)

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module network 'modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    location: location
    suffix: suffix
    tags: tags
    createPrivateDns: isPrivate
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    suffix: suffix
    tags: tags
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  scope: rg
  params: {
    location: location
    suffix: suffix
    tags: tags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    suffix: suffix
    tags: tags
    isPrivate: isPrivate
    uamiPrincipalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  scope: rg
  params: {
    location: cosmosLocation
    suffix: suffix
    tags: tags
    isPrivate: isPrivate
    uamiPrincipalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    location: location
    tags: tags
    accountName: foundryAccountName
    projectName: foundryProjectName
    isPrivate: isPrivate
    agentSubnetId: network.outputs.agentsSubnetId
    uamiPrincipalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
    claudeOrganizationName: claudeOrganizationName
    claudeCountryCode: claudeCountryCode
    claudeIndustry: claudeIndustry
    sonnetCapacity: claudeSonnetCapacity
    opusCapacity: claudeOpusCapacity
    haikuCapacity: claudeHaikuCapacity
    cuCompletionCapacity: cuCompletionCapacity
    embeddingCapacity: embeddingCapacity
  }
}

module search 'modules/search.bicep' = {
  name: 'search'
  scope: rg
  params: {
    location: location
    suffix: suffix
    tags: tags
    isPrivate: isPrivate
    foundryAccountName: foundry.outputs.accountName
    foundryAccountId: foundry.outputs.accountId
    foundryAccountPrincipalId: foundry.outputs.accountPrincipalId
    uamiPrincipalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    location: location
    suffix: suffix
    tags: tags
    uamiPrincipalId: identity.outputs.principalId
  }
}

module peFoundry 'modules/private-endpoint.bicep' = if (isPrivate) {
  name: 'pe-foundry'
  scope: rg
  params: {
    name: 'pe-foundry-${suffix}'
    location: location
    tags: tags
    subnetId: network.outputs.peSubnetId
    targetId: foundry.outputs.accountId
    groupIds: [ 'account' ]
    dnsZoneIds: [
      network.outputs.zoneIds.cognitiveServices
      network.outputs.zoneIds.openAi
      network.outputs.zoneIds.servicesAi
    ]
  }
}

module peSearch 'modules/private-endpoint.bicep' = if (isPrivate) {
  name: 'pe-search'
  scope: rg
  params: {
    name: 'pe-search-${suffix}'
    location: location
    tags: tags
    subnetId: network.outputs.peSubnetId
    targetId: search.outputs.searchId
    groupIds: [ 'searchService' ]
    dnsZoneIds: [
      network.outputs.zoneIds.search
    ]
  }
}

module peCosmos 'modules/private-endpoint.bicep' = if (isPrivate) {
  name: 'pe-cosmos'
  scope: rg
  params: {
    name: 'pe-cosmos-${suffix}'
    location: location
    tags: tags
    subnetId: network.outputs.peSubnetId
    targetId: cosmos.outputs.cosmosId
    groupIds: [ 'Sql' ]
    dnsZoneIds: [
      network.outputs.zoneIds.documents
    ]
  }
}

module peStorageApp 'modules/private-endpoint.bicep' = if (isPrivate) {
  name: 'pe-storage-app'
  scope: rg
  params: {
    name: 'pe-stapp-${suffix}'
    location: location
    tags: tags
    subnetId: network.outputs.peSubnetId
    targetId: storage.outputs.appStorageId
    groupIds: [ 'blob' ]
    dnsZoneIds: [
      network.outputs.zoneIds.blob
    ]
  }
}

module peStorageAgent 'modules/private-endpoint.bicep' = if (isPrivate) {
  name: 'pe-storage-agent'
  scope: rg
  params: {
    name: 'pe-stagent-${suffix}'
    location: location
    tags: tags
    subnetId: network.outputs.peSubnetId
    targetId: storage.outputs.agentStorageId
    groupIds: [ 'blob' ]
    dnsZoneIds: [
      network.outputs.zoneIds.blob
    ]
  }
}

module agentConnections 'modules/agent-connections.bicep' = {
  name: 'agent-connections'
  scope: rg
  params: {
    location: location
    accountName: foundry.outputs.accountName
    projectName: foundry.outputs.projectName
    projectPrincipalId: foundry.outputs.projectPrincipalId
    agentHostEnabled: agentHostEnabled
    appInsightsName: monitoring.outputs.appInsightsName
    searchName: search.outputs.searchName
    searchEndpoint: search.outputs.searchEndpoint
    cosmosName: cosmos.outputs.cosmosName
    cosmosEndpoint: cosmos.outputs.cosmosEndpoint
    agentStorageName: storage.outputs.agentStorageName
    agentStorageBlobEndpoint: storage.outputs.agentBlobEndpoint
  }
}

var appEnv = [
  {
    name: 'NETWORK_MODE'
    value: networkMode
  }
  {
    name: 'DI_ENDPOINT'
    value: 'https://${foundryAccountName}.cognitiveservices.azure.com/'
  }
  {
    name: 'CU_ENDPOINT'
    value: 'https://${foundryAccountName}.cognitiveservices.azure.com/'
  }
  {
    name: 'FOUNDRY_ENDPOINT'
    value: foundry.outputs.endpoint
  }
  {
    name: 'FOUNDRY_PROJECT_ENDPOINT'
    value: foundry.outputs.projectEndpoint
  }
  {
    name: 'CLAUDE_BASE_URL'
    value: 'https://${foundryAccountName}.services.ai.azure.com/anthropic'
  }
  {
    name: 'CLAUDE_SONNET_DEPLOYMENT'
    value: 'claude-sonnet-5-5'
  }
  {
    name: 'CLAUDE_OPUS_DEPLOYMENT'
    value: 'claude-opus-5-5'
  }
  {
    name: 'CLAUDE_HAIKU_DEPLOYMENT'
    value: 'claude-haiku-5-5'
  }
  {
    name: 'CU_COMPLETION_DEPLOYMENT'
    value: 'gpt-4.1'
  }
  {
    name: 'EMBEDDING_DEPLOYMENT'
    value: 'text-embedding-3-large'
  }
  {
    name: 'SEARCH_ENDPOINT'
    value: search.outputs.searchEndpoint
  }
  {
    name: 'SEARCH_INDEX_NAME'
    value: 'loan-documents'
  }
  {
    name: 'KNOWLEDGE_BASE_NAME'
    value: 'loan-kb'
  }
  {
    name: 'COSMOS_ENDPOINT'
    value: cosmos.outputs.cosmosEndpoint
  }
  {
    name: 'COSMOS_DATABASE'
    value: 'idp'
  }
  {
    name: 'STORAGE_BLOB_URL'
    value: storage.outputs.appBlobEndpoint
  }
  {
    name: 'AZURE_CLIENT_ID'
    value: identity.outputs.clientId
  }
  {
    name: 'TENANT_ID_HEADER'
    value: 'x-tenant-id'
  }
]

module aca 'modules/aca.bicep' = {
  name: 'aca'
  scope: rg
  params: {
    location: location
    tags: tags
    suffix: suffix
    isPrivate: isPrivate
    acaSubnetId: network.outputs.acaSubnetId
    uamiId: identity.outputs.id
    acrLoginServer: acr.outputs.loginServer
    apiImage: apiImage
    appInsightsName: monitoring.outputs.appInsightsName
    envVars: appEnv
  }
}

output resourceGroupName string = rg.name
output networkMode string = networkMode
output foundryAccountName string = foundry.outputs.accountName
output foundryProjectName string = foundry.outputs.projectName
output foundryEndpoint string = foundry.outputs.endpoint
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output searchEndpoint string = search.outputs.searchEndpoint
output cosmosEndpoint string = cosmos.outputs.cosmosEndpoint
output appBlobEndpoint string = storage.outputs.appBlobEndpoint
output acrLoginServer string = acr.outputs.loginServer
output uamiClientId string = identity.outputs.clientId
output apiFqdn string = aca.outputs.apiFqdn
