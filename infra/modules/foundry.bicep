// Foundry (AIServices) resource with project management, network injection (private mode),
// Claude and OpenAI deployments on Data Zone Standard (US), and RBAC for the app identity.
// Adapted from Microsoft's foundry-samples and Azure-Samples/claude (MIT). See infra/NOTICE.md.
param location string
param tags object
param accountName string
param projectName string
param isPrivate bool
param agentSubnetId string
param uamiPrincipalId string
param deployerPrincipalId string
param claudeOrganizationName string
param claudeCountryCode string
param claudeIndustry string
param sonnetCapacity int
param opusCapacity int
param haikuCapacity int
param cuCompletionCapacity int
param embeddingCapacity int

var modelProviderData = {
  organizationName: claudeOrganizationName
  countryCode: claudeCountryCode
  industry: claudeIndustry
}
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var cognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: isPrivate ? 'Disabled' : 'Enabled'
    networkInjections: isPrivate ? [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
    ] : null
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectName
    description: 'Loan document IDP: Claude agents, Foundry IQ retrieval and Content Understanding.'
  }
}

// Foundry serializes deployments under one account, so they are chained to avoid concurrent-create conflicts.
resource haikuDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: account
  name: 'claude-haiku-5-5'
  sku: {
    name: 'DataZoneStandard'
    capacity: haikuCapacity
  }
  properties: {
    model: {
      format: 'Anthropic'
      name: 'claude-haiku-5-5'
      version: '2'
    }
    modelProviderData: modelProviderData
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    project
  ]
}

resource opusDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: account
  name: 'claude-opus-5-5'
  sku: {
    name: 'DataZoneStandard'
    capacity: opusCapacity
  }
  properties: {
    model: {
      format: 'Anthropic'
      name: 'claude-opus-5-5'
      version: '2'
    }
    modelProviderData: modelProviderData
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    haikuDeployment
  ]
}

resource sonnetDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: account
  name: 'claude-sonnet-5-5'
  sku: {
    name: 'DataZoneStandard'
    capacity: sonnetCapacity
  }
  properties: {
    model: {
      format: 'Anthropic'
      name: 'claude-sonnet-5-5'
      version: '2'
    }
    modelProviderData: modelProviderData
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    opusDeployment
  ]
}

resource cuCompletionDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: account
  name: 'gpt-4.1'
  sku: {
    name: 'DataZoneStandard'
    capacity: cuCompletionCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1'
      version: '2025-04-14'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    sonnetDeployment
  ]
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: account
  name: 'text-embedding-3-large'
  sku: {
    name: 'DataZoneStandard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    cuCompletionDeployment
  ]
}

resource uamiCognitiveServicesRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, uamiPrincipalId, cognitiveServicesUser)
  scope: account
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
  }
}

resource uamiFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, uamiPrincipalId, foundryUser)
  scope: account
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUser)
  }
}

resource uamiOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, uamiPrincipalId, cognitiveServicesOpenAiUser)
  scope: account
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUser)
  }
}

resource deployerCognitiveServicesRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(account.id, deployerPrincipalId, cognitiveServicesUser)
  scope: account
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
  }
}

resource deployerFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(account.id, deployerPrincipalId, foundryUser)
  scope: account
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUser)
  }
}

output accountId string = account.id
output accountName string = account.name
output accountPrincipalId string = account.identity.principalId
output projectName string = project.name
output projectId string = project.id
output projectPrincipalId string = project.identity.principalId
output endpoint string = account.properties.endpoint
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
