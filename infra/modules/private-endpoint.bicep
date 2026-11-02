// Private endpoint plus DNS zone group for one target resource.
param name string
param location string
param tags object
param subnetId string
param targetId string
param groupIds array
param dnsZoneIds array

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${name}-connection'
        properties: {
          privateLinkServiceId: targetId
          groupIds: groupIds
        }
      }
    ]
  }
}

resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for (zoneId, i) in dnsZoneIds: {
      name: 'zone-${i}'
      properties: {
        privateDnsZoneId: zoneId
      }
    }]
  }
}
