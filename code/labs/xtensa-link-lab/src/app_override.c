/* A second strong definition used only to study GNU ld symbol precedence. */

__attribute__((noinline))
int lab_validator(int kind, int subtype, int length, int flags)
{
    return kind ^ subtype ^ length ^ flags ^ 0x200;
}

/* A unique strong alias lets nm identify the selected implementation. */
extern int app_validator_entry(int, int, int, int)
    __attribute__((alias("lab_validator")));
