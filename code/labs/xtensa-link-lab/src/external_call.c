/* This object has an undefined reference, so GNU ld --wrap can rewrite it. */

extern int lab_validator(int, int, int, int);

__attribute__((noinline))
int separate_object_call(int seed)
{
    return lab_validator(seed, 2, 3, 4);
}
