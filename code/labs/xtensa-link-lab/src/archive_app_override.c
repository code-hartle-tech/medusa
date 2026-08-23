/* Earlier application definition of the archive member's validator symbol. */

__attribute__((noinline))
int archive_validator(int value)
{
    return value + 101;
}

extern int app_archive_validator_entry(int value)
    __attribute__((alias("archive_validator")));
