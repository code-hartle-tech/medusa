/* This undefined public reference is what makes ld extract the archive member. */

extern int vendor_public_entry(int value);

__attribute__((noinline))
int archive_public_caller(void)
{
    return vendor_public_entry(5);
}
