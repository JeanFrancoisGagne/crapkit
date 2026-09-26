function Invoke-Build($x) {
    if ($x) {
        dotnet build --configuration $x.Configuration
    }
    return 0
}

function Get-Next($y) {
    return $y
}
