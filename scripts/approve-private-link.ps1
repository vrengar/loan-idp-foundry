<#
.SYNOPSIS
Approves pending private endpoint connections on a Foundry account.

.DESCRIPTION
The AI Search shared private link (integrated vectorizer) creates a pending connection on the
Foundry account. Run this after the P1 deployment. Requires Azure CLI sign-in with access to the account.
#>
param(
    [Parameter(Mandatory)][string]$SubscriptionId,
    [Parameter(Mandatory)][string]$ResourceGroup,
    [Parameter(Mandatory)][string]$AccountName
)

$url = "https://management.azure.com/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$AccountName/privateEndpointConnections?api-version=2025-04-01-preview"
$connections = (az rest --method get --url $url -o json | ConvertFrom-Json).value
$pending = @($connections | Where-Object { $_.properties.privateLinkServiceConnectionState.status -eq 'Pending' })
Write-Output ("pending connections: " + $pending.Count)

foreach ($connection in $pending) {
    az network private-endpoint-connection approve --id $connection.id --description "Approved for AI Search integrated vectorization" | Out-Null
    Write-Output ("approved: " + $connection.name)
}
