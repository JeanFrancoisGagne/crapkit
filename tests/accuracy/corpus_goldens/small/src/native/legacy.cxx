// A .cxx translation unit.
int legacy_score(int hits, int misses, bool strict)
{
    if (hits + misses == 0) {
        return 0;
    }
    if (strict && misses > 0) {
        return -misses;
    }
    return hits * 100 / (hits + misses);
}
