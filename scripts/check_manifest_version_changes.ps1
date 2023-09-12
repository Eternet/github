param (
    [string]$SfApp,
    [string]$ApplicationManifestFile
)

# Check if the XML files exists
if (-not (Test-Path $ApplicationManifestFile)) {
    throw "ApplicationManifestFile not found"
}

function Is-GreaterVersion { param ([string]$version1, [string]$version2)

    $ver1 = [version]$version1
    $ver2 = [version]$version2
    return $ver1 -gt $ver2
}

# Load the XML file
$xml = [xml](Get-Content $ApplicationManifestFile)

# Get the ApplicationManifest element
$applicationManifest = $xml.ApplicationManifest

# Get the current ApplicationTypeVersion
Connect-ServiceFabricCluster -ConnectionEndpoint "servicefabric.eternet.cc:19000"
$app = Get-ServiceFabricApplication -ApplicationName $SfApp -ErrorAction SilentlyContinue
$currentApplicationTypeVersion = $app.ApplicationTypeVersion

# Get the value of the ApplicationTypeVersion attribute
$applicationTypeVersion = $applicationManifest.ApplicationTypeVersion
if (Is-GreaterVersion $applicationTypeVersion $currentApplicationTypeVersion) 
{
   echo "versionChanges=true" >> $GITHUB_OUTPUT
   echo "Manifest version changes"
}
else 
{
   echo "versionChanges=false" >> $GITHUB_OUTPUT
   echo "Manifest version not changes"
}
