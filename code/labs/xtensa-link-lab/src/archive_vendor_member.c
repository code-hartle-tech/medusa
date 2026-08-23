/* One harmless stand-in member for a precompiled vendor static archive. */

__attribute__((noinline))
int archive_validator(int value)
{
    return value + 7;
}

extern int vendor_archive_validator_entry(int value)
    __attribute__((alias("archive_validator")));

__attribute__((noinline))
int vendor_public_entry(int value)
{
    return archive_validator(value) + 3;
}
