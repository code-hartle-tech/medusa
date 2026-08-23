/* GNU ld maps __real_lab_validator back to the unwrapped definition. */

extern int __real_lab_validator(int, int, int, int);

__attribute__((noinline))
int __wrap_lab_validator(int kind, int subtype, int length, int flags)
{
    return __real_lab_validator(kind, subtype, length, flags) + 0x300;
}

extern int wrapper_entry(int, int, int, int)
    __attribute__((alias("__wrap_lab_validator")));
