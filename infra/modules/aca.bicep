// Container Apps environment (workload profiles, inside the VNet in private mode) and the API app.
// The UI keeps external ingress; every backend is reached privately.
param location string
param tags object
param suffix string
param isPrivate bool
param acaSubnetId string
param uamiId string
param acrLoginServer string
param apiImage string
param appInsightsName string
param envVars array

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-idp-${suffix}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: isPrivate ? {
      infrastructureSubnetId: acaSubnetId
      internal: false
    } : null
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-idp-api'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acrLoginServer
          identity: uamiId
        }
      ]
      secrets: [
        {
          name: 'appi-connection-string'
          value: appInsights.properties.ConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          env: concat(envVars, [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appi-connection-string'
            }
          ])
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 2
        maxReplicas: 4
      }
    }
  }
}

output apiFqdn string = api.properties.configuration.ingress.fqdn
