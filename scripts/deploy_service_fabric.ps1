param (
    [string]$serviceFabricProjectPath,
    [string]$connectionEndpoint
)

$configurationFile = "Cloud.xml"
$PublishProfileFile = "$serviceFabricProjectPath\PublishProfiles\$configurationFile"
$ApplicationPackagePath = "$serviceFabricProjectPath\pkg\Release"
$StartupServicesFile = "$serviceFabricProjectPath\StartupServices.xml"
$deployScriptPath = "$serviceFabricProjectPath\Scripts\Deploy-FabricApplication.ps1"
$OverwriteBehavior = 'Always'

try {
    Import-Module ServiceFabric
    Connect-ServiceFabricCluster -ConnectionEndpoint $connectionEndpoint
    
    & $deployScriptPath `
        -PublishProfileFile $PublishProfileFile `
        -ApplicationPackagePath $ApplicationPackagePath `
        -StartupServicesFile $StartupServicesFile `
        -OverwriteBehavior $OverwriteBehavior `
        -SkipPackageValidation
}
catch {
    Write-Error "Deployment failed: $_"
    exit 1
}
