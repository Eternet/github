[CmdletBinding()]
param(
    [string]$DotnetVersion = '',
    [string]$GlobalJsonFile = '',
    [string]$WorkingDirectory = '.',
    [string]$Workspace = $env:GITHUB_WORKSPACE,
    [string]$DotnetCommand = 'dotnet',
    [ValidateSet('source','last-requested')][string]$SdkSelection = 'source',
    [string]$RuntimeVersion = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH = 'false'
$provisioningIssue = 'https://github.com/Eternet/Servidores/issues/554'

function Stop-Readiness([string]$Reason) {
    throw "$Reason Provision the shared runner through $provisioningIssue. This check never installs SDKs."
}

function Get-SelectorPattern([string]$Selector) {
    # setup-dotnet callers use exact SDK versions, major/minor channels, x/* and feature-band wildcards.
    if ($Selector -notmatch '^(?<core>\d+(?:\.(?:\d+[xX]*|[xX*])){0,2})(?<suffix>-[0-9A-Za-z.-]+)?$') {
        Stop-Readiness "Unsupported SDK version selector '$Selector'."
    }
    $core = $Matches['core']
    $suffix = $Matches['suffix']
    $parts = @($core.Split('.'))
    $patterns = @()
    foreach ($part in $parts) {
        if ($part -match '^[xX*]$') {
            $patterns += '\d+'
        } elseif ($part -match '^(?<prefix>\d+)(?<wildcards>[xX]+)$') {
            $patterns += [regex]::Escape($Matches['prefix']) + '\d{' + $Matches['wildcards'].Length + '}'
        } else {
            $patterns += [regex]::Escape($part)
        }
    }
    while ($patterns.Count -lt 3) { $patterns += '\d+' }
    return '^' + ($patterns -join '\.') + [regex]::Escape($suffix) + '$'
}

function Invoke-ReadinessDotnet([string[]]$Arguments) {
    $global:LASTEXITCODE = 0
    try {
        $lines = @(& $script:dotnetPath @Arguments 2>&1 | ForEach-Object { $_.ToString() })
        $commandExit = $LASTEXITCODE
    } catch {
        Stop-Readiness "Could not execute dotnet $($Arguments -join ' ')."
    }
    return [pscustomobject]@{ ExitCode = $commandExit; Lines = $lines }
}

if ([string]::IsNullOrWhiteSpace($DotnetVersion) -and [string]::IsNullOrWhiteSpace($GlobalJsonFile)) {
    Stop-Readiness 'Specify dotnet-version or global-json-file; an SDK version must not be inferred.'
}
if ([string]::IsNullOrWhiteSpace($Workspace)) { $Workspace = (Get-Location).Path }
$workspacePath = [IO.Path]::GetFullPath($Workspace)
if (-not (Test-Path -LiteralPath $workspacePath -PathType Container)) {
    Stop-Readiness 'The source workspace does not exist. Check out the source before checking SDK readiness.'
}
if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) { $WorkingDirectory = '.' }
$sourcePath = if ([IO.Path]::IsPathRooted($WorkingDirectory)) {
    [IO.Path]::GetFullPath($WorkingDirectory)
} else {
    [IO.Path]::GetFullPath((Join-Path $workspacePath $WorkingDirectory))
}
if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
    Stop-Readiness 'The requested source checkout directory does not exist.'
}

$requests = @($DotnetVersion -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$requirements = @($requests | ForEach-Object {
    [pscustomobject]@{ Selector = $_; Pattern = (Get-SelectorPattern $_) }
})
$runtimeRequirements = @($RuntimeVersion -split '\r?\n' | ForEach-Object {
    $line=$_.Trim()
    if($line){
        if($line -notmatch '^(?<name>Microsoft\.(?:NETCore|AspNetCore|WindowsDesktop)\.App)\s+(?<selector>\S+)$'){
            Stop-Readiness "Unsupported runtime requirement '$line'."
        }
        $name=$Matches['name'];$selector=$Matches['selector']
        [pscustomobject]@{Name=$name;Selector=$selector;Pattern=(Get-SelectorPattern $selector)}
    }
})
if($SdkSelection -ceq 'last-requested' -and $requirements.Count -eq 0){
    Stop-Readiness 'last-requested SDK selection requires dotnet-version.'
}

$globalPath=$null
if (-not [string]::IsNullOrWhiteSpace($GlobalJsonFile)) {
    $globalPath = if ([IO.Path]::IsPathRooted($GlobalJsonFile)) {
        [IO.Path]::GetFullPath($GlobalJsonFile)
    } else {
        [IO.Path]::GetFullPath((Join-Path $workspacePath $GlobalJsonFile))
    }
    if (-not (Test-Path -LiteralPath $globalPath -PathType Leaf)) {
        Stop-Readiness "The source global.json '$GlobalJsonFile' does not exist."
    }
    if ([IO.Path]::GetFileName($globalPath) -cne 'global.json') {
        Stop-Readiness 'global-json-file must point to the actual source global.json used by the SDK resolver.'
    }
    try { $globalSettings = Get-Content -LiteralPath $globalPath -Raw | ConvertFrom-Json }
    catch { Stop-Readiness 'The source global.json is not valid JSON.' }
    if ($null -eq $globalSettings) { Stop-Readiness 'The source global.json does not declare an SDK version.' }
    $sdkProperty = $globalSettings.PSObject.Properties['sdk']
    if ($null -eq $sdkProperty -or $null -eq $sdkProperty.Value) {
        Stop-Readiness 'The requested global.json does not declare an SDK version.'
    }
    $versionProperty = $sdkProperty.Value.PSObject.Properties['version']
    if ($null -eq $versionProperty -or $versionProperty.Value -isnot [string] -or
        $versionProperty.Value -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
        Stop-Readiness 'The source global.json must declare a concrete sdk.version.'
    }
    # Delegate rollForward/allowPrerelease and resolver errors to the installed SDK host.
    $sourcePath = Split-Path -Parent $globalPath
}else{
    # Respect implicit source/ancestor global.json too; never replace its SDK policy.
    $ancestor=[IO.DirectoryInfo]::new($sourcePath)
    while($null -ne $ancestor){
        $candidate=Join-Path $ancestor.FullName 'global.json'
        if(Test-Path -LiteralPath $candidate -PathType Leaf){$globalPath=$candidate;break}
        $ancestor=$ancestor.Parent
    }
}

$command = Get-Command -Name $DotnetCommand -CommandType Application, ExternalScript -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -eq $command) { Stop-Readiness 'dotnet is not available on this runner.' }
$script:dotnetPath = $command.Source
$hostItem=Get-Item -LiteralPath $script:dotnetPath -ErrorAction Stop
if($hostItem.Attributes -band [IO.FileAttributes]::ReparsePoint){$hostItem=$hostItem.ResolveLinkTarget($true)}
if(-not $hostItem -or -not $hostItem.Exists){Stop-Readiness 'The provisioned dotnet host target does not exist.'}
$dotnetRoot=[IO.Path]::GetDirectoryName($hostItem.FullName)
if($dotnetRoot -match '[\r\n]'){Stop-Readiness 'The provisioned dotnet root must be a single-line path.'}
$previousLocation = Get-Location
$selectionRoot=$null;$requestedSelection=$null
try {
    Set-Location -LiteralPath $sourcePath
    $listing = Invoke-ReadinessDotnet @('--list-sdks')
    if ($listing.ExitCode -ne 0) { Stop-Readiness 'dotnet --list-sdks failed.' }
    $installed = @($listing.Lines | ForEach-Object {
        if ($_ -match '^\s*(?<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\s+\[.+\]\s*$') {
            $Matches['version']
        }
    } | Sort-Object -Unique)
    if ($installed.Count -eq 0) { Stop-Readiness 'No installed .NET SDKs were reported by dotnet --list-sdks.' }
    foreach ($requirement in $requirements) {
        if (@($installed | Where-Object { $_ -match $requirement.Pattern }).Count -eq 0) {
            Stop-Readiness "Required SDK '$($requirement.Selector)' is missing. Installed SDK versions: $($installed -join ', ')."
        }
    }
    if($SdkSelection -ceq 'last-requested' -and -not $globalPath){
        $last=$requirements[-1]
        $requestedSelection=@($installed|Where-Object{$_ -match $last.Pattern}|Sort-Object {[Management.Automation.SemanticVersion]$_} -Descending)[0]
        # Verify the exact candidate through the native SDK resolver in an owned
        # temporary directory. Source files and persistent environment stay intact.
        $temporaryParent=if($env:RUNNER_TEMP){$env:RUNNER_TEMP}else{[IO.Path]::GetTempPath()}
        $temporary=Join-Path $temporaryParent ('dotnet-readiness-selection-'+[Guid]::NewGuid().ToString('N'))
        $null=New-Item -ItemType Directory -Path $temporary -ErrorAction Stop
        $selectionRoot=$temporary
        $pin=@{sdk=@{version=$requestedSelection;rollForward='disable';allowPrerelease=$requestedSelection.Contains('-')}}|ConvertTo-Json
        [IO.File]::WriteAllText((Join-Path $selectionRoot 'global.json'),$pin,[Text.UTF8Encoding]::new($false))
        Set-Location -LiteralPath $selectionRoot
    }
    $resolution = Invoke-ReadinessDotnet @('--version')
    if ($resolution.ExitCode -ne 0) {
        Stop-Readiness 'The installed dotnet host cannot resolve an SDK for the source checkout/global.json.'
    }
    $selected = @($resolution.Lines | Where-Object { $_ -match '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$' })
    if ($selected.Count -ne 1 -or $installed -notcontains $selected[0]) {
        Stop-Readiness 'dotnet --version did not identify exactly one SDK from the installed inventory.'
    }
    if($requestedSelection -and $selected[0] -cne $requestedSelection){
        Stop-Readiness 'The installed dotnet host did not resolve the requested SDK candidate.'
    }
    if($runtimeRequirements.Count){
        $runtimeListing=Invoke-ReadinessDotnet @('--list-runtimes')
        if($runtimeListing.ExitCode -ne 0){Stop-Readiness 'dotnet --list-runtimes failed.'}
        $runtimes=@($runtimeListing.Lines|ForEach-Object{
            if($_ -match '^\s*(?<name>Microsoft\.[A-Za-z.]+)\s+(?<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\s+\[.+\]\s*$'){
                [pscustomobject]@{Name=$Matches['name'];Version=$Matches['version']}
            }
        })
        foreach($requirement in $runtimeRequirements){
            if(@($runtimes|Where-Object{$_.Name -ceq $requirement.Name -and $_.Version -match $requirement.Pattern}).Count -eq 0){
                Stop-Readiness "Required runtime '$($requirement.Name) $($requirement.Selector)' is missing."
            }
        }
    }
    Write-Host "Provisioned .NET SDK readiness passed. Selected SDK: $($selected[0])."
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_OUTPUT)) {
        "dotnet-version=$($selected[0])" | Add-Content -LiteralPath $env:GITHUB_OUTPUT -Encoding utf8
        "dotnet-root=$dotnetRoot" | Add-Content -LiteralPath $env:GITHUB_OUTPUT -Encoding utf8
    }
} finally {
    Set-Location -LiteralPath $previousLocation.Path
    if($selectionRoot){[IO.File]::Delete((Join-Path $selectionRoot 'global.json'));[IO.Directory]::Delete($selectionRoot,$false)}
}
