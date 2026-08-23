/*
 * A deliberately boring, non-radio stand-in for a vendor policy function.
 *
 * Keeping the definition and same_object_call() in this one translation unit
 * is essential to the --wrap experiment.
 */

__attribute__((noinline))
int lab_validator(int kind, int subtype, int length, int flags)
{
    return kind + subtype + length - flags + 0x100;
}

/* A unique strong alias lets nm prove which body owns lab_validator. */
extern int vendor_validator_entry(int, int, int, int)
    __attribute__((alias("lab_validator")));

__attribute__((noinline))
int same_object_call(int seed)
{
    return lab_validator(seed, 2, 3, 4);
}
