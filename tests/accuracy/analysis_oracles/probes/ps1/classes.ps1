function Get-WithClass($x) {
    class Holder {
        [int] Pick([int]$y) {
            if ($y) {
                return 1
            }
            return 0
        }
    }
    return $x
}
