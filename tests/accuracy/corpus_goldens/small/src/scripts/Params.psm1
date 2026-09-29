# A module whose functions declare their parameters in param() blocks.
function Invoke-Build {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Target,
        [string] $Configuration = "Release",
        [switch] $Clean
    )
    if ($Clean) {
        Write-Verbose "cleaning $Target"
    }
    foreach ($step in @("restore", "build")) {
        if ($step -eq "build" -and $Configuration -eq "Debug") {
            Write-Verbose "debug build"
        }
    }
}

Export-ModuleMember -Function Invoke-Build
