// VNet with Container Apps, Foundry agent injection and private endpoint subnets, plus private DNS zones.
// Adapted from Microsoft's foundry-samples (MIT). See infra/NOTICE.md.
param location string
param suffix string
param tags object
param createPrivateDns bool

var blobZoneName = 'privatelink.blob.${environment().suffixes.storage}'

var zoneNames = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
  'privatelink.search.windows.net'
  'privatelink.documents.azure.com'
  blobZoneName
]

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-idp-${suffix}'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.60.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: '10.60.0.0/24'
          delegations: [
            {
              name: 'container-apps'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'snet-agents'
        properties: {
          addressPrefix: '10.60.1.0/24'
          delegations: [
            {
              name: 'foundry-agents'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: '10.60.2.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for zone in zoneNames: if (createPrivateDns) {
  name: zone
  location: 'global'
  tags: tags
}]

resource zoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for i in range(0, length(zoneNames)): if (createPrivateDns) {
  parent: zones[i]
  name: 'link-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}]

output acaSubnetId string = vnet.properties.subnets[0].id
output agentsSubnetId string = vnet.properties.subnets[1].id
output peSubnetId string = vnet.properties.subnets[2].id
output zoneIds object = {
  cognitiveServices: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.cognitiveservices.azure.com')
  openAi: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.openai.azure.com')
  servicesAi: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.services.ai.azure.com')
  search: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.search.windows.net')
  documents: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.documents.azure.com')
  blob: resourceId('Microsoft.Network/privateDnsZones', blobZoneName)
}
