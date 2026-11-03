function Get-RunnerProcessPath {
    [CmdletBinding()]
    param(
        [AllowEmptyString()][string]$PathValue,
        [bool]$Windows = $IsWindows,
        [scriptblock]$DirectoryExists = { param($Directory) Test-Path -LiteralPath $Directory -PathType Container -ErrorAction Stop }
    )
    if ($PathValue -match '[\r\n\x00]') {
        throw 'The inherited process PATH must be a single-line value.'
    }
    $removed = 0
    $result = $PathValue
    if ($Windows) {
        # Only the verified first-use pollution pattern of the shared Windows runners.
        # Keep live directories, unrelated missing paths, order, duplicates and empty entries.
        $temporaryTools = '(?i)^[a-z]:\\actions-runner(?:-\d+)?\\_work\\_temp\\[^;]+\\\.dotnet\\tools\\?$'
        $retained = foreach ($entry in ($PathValue -split ';')) {
            if ($entry -match $temporaryTools -and -not (& $DirectoryExists $entry)) {
                $removed++
            } else {
                $entry
            }
        }
        $result = $retained -join ';'
        if ($result.Length -gt 8191) {
            throw 'The remaining process PATH exceeds cmd.exe limit 8191. Repair the shared profile; unrelated entries were preserved.'
        }
    }
    [pscustomobject]@{ Path = $result; Removed = $removed; OriginalLength = $PathValue.Length; Length = $result.Length }
}

function Initialize-RunnerProcessEnvironment {
    [CmdletBinding()]
    param(
        [bool]$Windows = $IsWindows,
        [scriptblock]$DirectoryExists = { param($Directory) Test-Path -LiteralPath $Directory -PathType Container -ErrorAction Stop }
    )
    $plan = Get-RunnerProcessPath -PathValue $env:PATH -Windows $Windows -DirectoryExists $DirectoryExists
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_ENV)) {
        'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH=false' | Add-Content -LiteralPath $env:GITHUB_ENV -Encoding utf8
        if ($plan.Removed -gt 0) {
            "PATH=$($plan.Path)" | Add-Content -LiteralPath $env:GITHUB_ENV -Encoding utf8
        }
    }
    # Process scope only. Never write HKCU/HKU, machine PATH, runner .env or services.
    $env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH = 'false'
    $env:PATH = $plan.Path
    Write-Host "Runner process environment ready: removed $($plan.Removed) nonexistent temporary tool entries; PATH length $($plan.Length)."
}
