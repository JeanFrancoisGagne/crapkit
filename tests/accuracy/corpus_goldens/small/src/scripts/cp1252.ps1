# Windows-1252 bytes: the e-acute below is one byte, 0xE9.
function Get-Café {
    param([string]$Sugar)
    if ($Sugar -eq "oui") {
        return "café sucré"
    }
    return "café"
}
