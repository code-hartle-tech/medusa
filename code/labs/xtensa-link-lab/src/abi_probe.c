/* Small enough that the register-window choreography is easy to disassemble. */

__attribute__((noinline))
int abi_sum4(int first, int second, int third, int fourth)
{
    return first + second + third + fourth;
}

__attribute__((noinline))
int abi_callsite(void)
{
    return abi_sum4(11, 22, 33, 44);
}
