---
layout: post
title: "Evaluating Claude's C Compiler Against GCC"
date: 2026-02-12
categories: [compilers, ai, systems]
tags: [claude, c-compiler, gcc, ai-generated-code, assembly, optimization]
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2Fcompilers%2Fai%2Fsystems%2F2026%2F02%2F12%2Fevaluating-claude-c-compiler.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# Evaluating Claude's C Compiler Against GCC

Anthropic recently announced that 16 instances of Claude Opus 4.6, running in parallel as autonomous agents, built a C compiler from scratch. Over nearly 2,000 Claude Code sessions across two weeks, at $20,000 in API costs, the agents produced a 100,000-line Rust codebase. A clean-room implementation with no internet access, depending only on the Rust standard library. According to Anthropic, it can build a bootable Linux 6.9 on x86, ARM, and RISC-V, compile projects like SQLite, Redis, Postgres, FFmpeg, and Doom, and has a 99% pass rate on the GCC torture test suite.

I could immediately see posts from two camps. One announced that this is a great feat and evidence of what autonomous AI agents can achieve on real systems-level projects. The other pointed out that a C compiler is a "solved problem" with endless training data on GitHub, and that this compiler still uses GCC's assembler and linker, and calls out to GCC for 16-bit x86 real mode boot code (though ARM and RISC-V are fully self-compiled).

Both camps are arguing about what this *means*. I wanted to see how it actually *performs*. I spun up a [Google Cloud Shell](https://shell.cloud.google.com/?pli=1&show=ide%2Cterminal), cloned the [repository](https://github.com/anthropics/claudes-c-compiler), built the compiler, and ran a series of stress tests head-to-head against GCC.

```bash
git clone https://github.com/anthropics/claudes-c-compiler.git
cd claudes-c-compiler
cargo build --release
export CCC=./target/release/ccc
```

Test machine: `Linux 6.6.111+ x86_64`. Every test follows the same pattern: compile with CCC, compile with GCC, compare outputs and binaries.

Here's what I found.

---

## Pointer Arithmetic & Struct Alignment

When you define a struct in C with mixed types, the compiler inserts padding bytes between fields to satisfy alignment requirements. On x86-64 Linux (the architecture we're testing on), the System V ABI requires an `int` to start at a 4-byte boundary and a `double` at an 8-byte boundary.
If a compiler gets this wrong, your struct fields land at the wrong memory offsets and everything downstream breaks i.e. pointer arithmetic, casting, serialization. Most of the other tests I run later w.r.t. function pointers, variadic functions and struct-heavy code depend on the compiler laying out memory correctly, so I started here.

```c
// test_pointer.c
#include <stdio.h>

struct Data {
    char a;      // 1 byte
    int b;       // 4 bytes (aligned to 4)
    double c;    // 8 bytes
};

int main() {
    struct Data d = {'Z', 42, 3.14159};
    struct Data *p = &d;

    char *base = (char*)p;
    int *p_int = (int*)(base + 4);
    double *p_dbl = (double*)(base + 8);

    printf("Compiler Access: %c, %d, %f\n", p->a, p->b, p->c);
    printf("Pointer Arith:   %c, %d, %f\n", *base, *p_int, *p_dbl);

    if (p->a == *base && p->b == *p_int && p->c == *p_dbl) {
        printf("RESULT: Pointer arithmetic PASSED\n");
    } else {
        printf("RESULT: Pointer arithmetic FAILED\n");
    }
    return 0;
}
```

```bash
$CCC test_pointer.c -o test_pointer_ccc && ./test_pointer_ccc
gcc test_pointer.c -o test_pointer_gcc && ./test_pointer_gcc
```

```
CCC: Compiler Access: Z, 42, 3.141590 | Pointer Arith: Z, 42, 3.141590  PASS
GCC: Compiler Access: Z, 42, 3.141590 | Pointer Arith: Z, 42, 3.141590  PASS
Binary: CCC 15K, GCC 16K
```

Identical output. The manual pointer offsets matched the compiler-generated field access exactly.

---

## Deep Recursion

Every time a function calls itself, the compiler needs to save the current state which includes local variables, return address, register values onto the stack in what's called a stack frame.
Recursive functions amplify this because the compiler has to manage many stack frames at once, one per call, each with its own set of saved values. I used a recursive factorial to test this.

```c
// test_recursion.c
#include <stdio.h>

long factorial(int n, int depth) {
    if (n <= 1) return 1;
    return n * factorial(n - 1, depth + 1);
}

int main() {
    long result = factorial(20, 0);
    printf("factorial(20) = %ld\n", result);
    printf("Expected:        %ld\n", 2432902008176640000L);

    if (result == 2432902008176640000L) {
        printf("RESULT: Recursion PASSED\n");
    } else {
        printf("RESULT: Recursion FAILED\n");
    }
    return 0;
}
```

```bash
$CCC test_recursion.c -o test_recursion_ccc && ./test_recursion_ccc
gcc test_recursion.c -o test_recursion_gcc && ./test_recursion_gcc
```

Both compilers produced the correct result for `n = 20`. But what happens under the hood? To find out, I compared the generated assembly and then pushed the recursion depth much higher.

```bash
# Assembly comparison
$CCC -S test_recursion.c -o test_recursion_ccc.s
gcc -S test_recursion.c -o test_recursion_gcc.s
gcc -O3 -S test_recursion.c -o test_recursion_gcc_o3.s

grep -c "call" test_recursion_ccc.s
grep -c "call" test_recursion_gcc.s
grep -c "call" test_recursion_gcc_o3.s

grep "factorial" test_recursion_gcc_o3.s
```

```
CCC:      6 call instructions
GCC:      6 call instructions
GCC -O3:  4 call instructions

GCC -O3 factorial symbol output:
    .globl  factorial
    .type   factorial, @function
    factorial:
    .size   factorial, .-factorial
    .string "factorial(20) = %ld\n"
```

Interesting. GCC at `-O3` reduced the call count from 6 to 4, but the `factorial` symbol is still present as a function with a `call` to itself. GCC did not fully eliminate the recursion into a loop here. This is because our `factorial` function is not tail-recursive: the last operation is `n * factorial(n - 1, depth + 1)`, which means the multiplication happens after the recursive call returns. GCC can only convert to a loop when the recursive call is truly the last thing the function does.

I then pushed the recursion depth to 10,000,000 to see what happens:

```c
// test_recursion_deep.c
#include <stdio.h>

long factorial(int n, int depth) {
    if (n <= 1) return 1;
    return n * factorial(n - 1, depth + 1);
}

int main() {
    printf("factorial(20) = %ld\n", factorial(20, 0));
    printf("factorial(10000000) = ...\n");
    long result = factorial(10000000, 0);
    printf("Completed with result (overflowed but didn't crash)\n");
    return 0;
}
```

```bash
$CCC test_recursion_deep.c -o test_recursion_deep_ccc && ./test_recursion_deep_ccc
gcc test_recursion_deep.c -o test_recursion_deep_gcc && ./test_recursion_deep_gcc
gcc -O3 test_recursion_deep.c -o test_recursion_deep_gcc_o3 && ./test_recursion_deep_gcc_o3
```

```
CCC:      factorial(20) printed, then Segmentation fault (core dumped)
GCC:      factorial(20) printed, then Segmentation fault (core dumped)
GCC -O3:  factorial(20) printed, factorial(10000000) completed (overflowed but no crash)
```

Both CCC and GCC (unoptimized) crashed with a segfault at 10 million levels of recursion. GCC at `-O3` survived. Going back to the assembly, GCC `-O3` had reduced the call count from 6 to 4. Even though the function is not tail-recursive (the multiplication happens after the recursive call returns), GCC `-O3` still optimized the stack usage enough to survive where both CCC and unoptimized GCC ran out of stack space.

CCC and GCC (unoptimized) generated the same number of `call` instructions and both hit the same wall. The difference only showed up when GCC's optimizer got involved.

---

## Assembly Inspection

The tests so far compared runtime output, which tells you if the compiler produces correct results. But two compilers can produce identical output while generating very different machine code underneath.
Looking at the assembly lets you see how efficiently the compiler translates your C into actual CPU instructions. I compiled a trivial `add_numbers(int a, int b)` function and compared the generated assembly.

```c
// test_asm.c
int add_numbers(int a, int b) {
    int c = a + b;
    return c;
}

int main() {
    return add_numbers(3, 4);
}
```

```bash
$CCC -S test_asm.c -o test_asm_ccc.s
gcc -S test_asm.c -o test_asm_gcc.s
gcc -O3 -S test_asm.c -o test_asm_gcc_o3.s
```

CCC's output:
```asm
add_numbers:
    pushq %rbp
    movq %rsp, %rbp
    subq $16, %rsp
    movq %rdi, -8(%rbp)       ; spill arg 'a' to stack
    movq %rsi, -16(%rbp)      ; spill arg 'b' to stack
    movslq -8(%rbp), %rax     ; load 'a' back from stack
    ...
```

GCC (unoptimized, `-O0`):
```asm
add_numbers:
    pushq   %rbp
    movq    %rsp, %rbp
    movl    %edi, -20(%rbp)    ; store 'a'
    movl    %esi, -24(%rbp)    ; store 'b'
```

GCC unoptimized is already tighter. It uses `movl` (4-byte move to 32-bit register) where CCC uses `movq` (8-byte move to 64-bit register). For `int` values, 32-bit operations are sufficient and produce shorter instructions.

A quick note on GCC's optimization flags: GCC accepts `-O0` through `-O3` to control how aggressively it optimizes. `-O0` (the default) does almost no optimization, so the assembly closely mirrors your source code. `-O3` enables everything GCC has: function inlining, loop unrolling, dead code elimination, register allocation, instruction scheduling, and more. The tradeoff is longer compile times for faster, smaller binaries.

At `-O3`, GCC compiles `add_numbers` down to essentially:
```asm
add_numbers:
    leal    (%rdi,%rsi), %eax  ; a + b, result in eax
    ret
```

One instruction for the addition, one to return. No stack frame, no spilling, no loading. The `leal` instruction computes the sum of the two registers directly.

The pattern I kept seeing in CCC: **Instruction Inflation**. CCC takes the arguments (which arrive in registers `%rdi` and `%rsi`), immediately spills them to the stack, then loads them back from the stack to do the addition. A good register allocator keeps values in registers for as long as possible. CCC does the opposite, moving values to memory after every operation and loading them back for the next one.

---

## Dead Code Elimination

When a compiler encounters code that can never execute, like a block inside `if (0)`, it can safely remove it from the final binary. This is called dead code elimination, and it matters because dead code increases binary size and can leak information (string literals, internal paths, debug messages) into production binaries. I wrote a program with an unreachable `if (0)` block containing a string literal and an unused variable to test whether CCC detects and removes these:

```c
// test_deadcode.c
#include <stdio.h>

int main() {
    int secret_number = 5555;       // dead store

    if (0) {
        printf("This message should NOT be in the binary.\n");
    }

    int active_var = 100;
    return active_var;
}
```

```bash
$CCC test_deadcode.c -o test_deadcode_ccc
gcc -O3 test_deadcode.c -o test_deadcode_gcc

echo "--- CCC ---"
strings test_deadcode_ccc | grep "should NOT"
echo "--- GCC ---"
strings test_deadcode_gcc | grep "should NOT"
```

```
CCC binary: "This message should NOT be in the binary." PRESENT
GCC binary: (nothing)
```

The string from the `if (0)` block was absent from the GCC binary but still present in the CCC binary. Interestingly, neither compiler kept the `5555` constant in the assembly, so unused scalar variables do get dropped by both. The difference is in the `if (0)` block: GCC recognized that the condition can never be true and removed the entire block, including the string. CCC compiled the block anyway and left the string in the binary.

---

## Constant Folding

The dead code test checked whether CCC removes code that can never run. Constant folding is a related question: can the compiler evaluate expressions at compile time instead of generating instructions to compute them at runtime?
If you write `24 * 60 * 60`, a compiler that does constant folding will put `86400` directly into the binary instead of emitting multiply instructions.

```c
// test_constfold.c
#include <stdio.h>

int main() {
    int seconds = 24 * 60 * 60;
    printf("Seconds in a day: %d\n", seconds);
    return 0;
}
```

```bash
$CCC -S test_constfold.c -o test_constfold_ccc.s
gcc -S test_constfold.c -o test_constfold_gcc.s

grep "86400" test_constfold_ccc.s
grep "86400" test_constfold_gcc.s
```

```
CCC: movq $86400, %rax
GCC: movl $86400, %edx
```

Both pre-calculated `86400` at compile time. This confirms the agents built a legitimate semantic analyzer. They're evaluating expressions, not doing text substitution. (CCC uses `movq` to a 64-bit register where GCC uses `movl` to a 32-bit register. Both work, but GCC's is one byte shorter in the instruction encoding.)

---

## Multi-File Compilation & Linking

So far, all the tests have been single-file programs. But real projects split code across multiple files, with header files declaring interfaces and separate `.c` files implementing them. The compiler needs to produce object files (`.o`) that can be linked together, resolving `extern` symbols across compilation units. I tested this with two source files sharing a global variable.

```c
// math_utils.h
#ifndef MATH_UTILS_H
#define MATH_UTILS_H
extern int add(int a, int b);
extern int multiply(int a, int b);
extern int global_counter;
#endif
```

```c
// math_utils.c
#include "math_utils.h"

int global_counter = 0;

int add(int a, int b) {
    global_counter++;
    return a + b;
}

int multiply(int a, int b) {
    global_counter++;
    return a * b;
}
```

```c
// test_multifile.c
#include <stdio.h>
#include "math_utils.h"

int main() {
    int sum = add(3, 4);
    int product = multiply(5, 6);
    int counter = global_counter;

    printf("add(3, 4) = %d\n", sum);
    printf("multiply(5, 6) = %d\n", product);
    printf("global_counter = %d (expected: 2)\n", counter);

    if (sum == 7 && product == 30 && counter == 2) {
        printf("RESULT: Multi-file compilation PASSED\n");
    } else {
        printf("RESULT: Multi-file compilation FAILED\n");
    }
    return 0;
}
```

```bash
# CCC
$CCC -c math_utils.c -o math_utils_ccc.o
$CCC -c test_multifile.c -o test_multifile_ccc.o
$CCC math_utils_ccc.o test_multifile_ccc.o -o test_multifile_ccc
./test_multifile_ccc

# GCC
gcc -c math_utils.c -o math_utils_gcc.o
gcc -c test_multifile.c -o test_multifile_gcc.o
gcc math_utils_gcc.o test_multifile_gcc.o -o test_multifile_gcc
./test_multifile_gcc
```

Both compilers produced the same output: `add(3,4) = 7`, `multiply(5,6) = 30`, `global_counter = 2`. CCC's `.o` files linked together without errors, and the `extern int global_counter` was correctly shared between the two compilation units.

---

## Preprocessor

The multi-file test depended on `#include` and `#ifndef` header guards working correctly, which they did. But the C preprocessor can do a lot more than include files and check definitions. It's essentially a text transformation layer that runs before the compiler sees your code, handling `#include`, `#define`, `#ifdef`, and macro expansion.
Most of this is straightforward, but there are some tricky features worth testing: stringification (`#`) converts a macro argument into a string literal, token pasting (`##`) glues two tokens into a single identifier, and variadic macros accept a variable number of arguments. I tested all of these along with nested `#ifdef/#ifndef`.

```c
// test_preprocessor.c
#include <stdio.h>

#define STRINGIFY(x) #x
#define TOSTRING(x) STRINGIFY(x)
#define CONCAT(a, b) a##b
#define MAKE_VAR(prefix, num) prefix##num
#define LOG(fmt, ...) printf("[LOG] " fmt "\n", ##__VA_ARGS__)

#define FEATURE_A
#define FEATURE_B

int main() {
    // Stringification
    int my_variable = 42;
    printf("Variable name: %s\n", STRINGIFY(my_variable));
    printf("Value of 100+200: %s\n", TOSTRING(100+200));

    // Token pasting
    int value1 = 10;
    int value2 = 20;
    printf("CONCAT result: %d\n", CONCAT(value, 1));
    printf("MAKE_VAR result: %d\n", MAKE_VAR(value, 2));

    // Variadic macro
    LOG("Simple message");
    LOG("Value is %d", 42);
    LOG("Two values: %d and %d", 10, 20);

    // Nested ifdef
    int result = 0;
#ifdef FEATURE_A
    result += 1;
    #ifdef FEATURE_B
        result += 10;
        #ifndef FEATURE_C
            result += 100;
        #endif
    #endif
#else
    result = -1;
#endif

    printf("Nested ifdef result: %d (expected: 111)\n", result);

    if (result == 111) {
        printf("RESULT: Preprocessor PASSED\n");
    } else {
        printf("RESULT: Preprocessor FAILED\n");
    }
    return 0;
}
```

```bash
$CCC test_preprocessor.c -o test_preprocessor_ccc && ./test_preprocessor_ccc
gcc test_preprocessor.c -o test_preprocessor_gcc && ./test_preprocessor_gcc

# Compare preprocessor output
$CCC -E test_preprocessor.c > preprocessed_ccc.txt
gcc -E test_preprocessor.c > preprocessed_gcc.txt
wc -l preprocessed_ccc.txt preprocessed_gcc.txt
```

Both compilers produced the same runtime output for all the macro tests. I then compared the raw preprocessor output using the `-E` flag, which dumps the fully expanded source before compilation. The actual macro expansions were identical, but the files were very different in size. CCC produced **3,361 lines** versus GCC's **855 lines** for the same source.
The extra lines in CCC's output were whitespace and line markers from header expansion. It doesn't affect the compiled result, but it shows that CCC's preprocessor is more verbose in how it processes `#include` files.

---

## Function Pointers & Indirect Calls

The preprocessor test verified that CCC handles text transformation correctly before compilation. The next question is how it handles something that's resolved much later: function pointers.
In C, functions have addresses, and you can store those addresses in variables, pass them as arguments, and call them indirectly. This is how callback patterns, plugin systems, and vtable-style dispatch work in C. For the compiler, this means it can't always know at compile time which function will be called.
It has to generate an indirect call instruction that jumps to whatever address is in the pointer at runtime. I tested three patterns: passing functions as callback arguments, storing function pointers in structs, and dispatching through arrays of function pointers.

```c
// test_function_pointers.c
#include <stdio.h>

int apply(int (*func)(int, int), int a, int b) {
    return func(a, b);
}

int add(int a, int b) { return a + b; }
int sub(int a, int b) { return a - b; }
int mul(int a, int b) { return a * b; }

typedef int (*operation_t)(int, int);

typedef struct {
    const char *name;
    operation_t func;
} NamedOp;

typedef int (*op_func)(int, int);

int main() {
    // Callback
    printf("apply(add, 10, 3) = %d (expected: 13)\n", apply(add, 10, 3));
    printf("apply(sub, 10, 3) = %d (expected: 7)\n", apply(sub, 10, 3));

    // Function pointer in struct
    NamedOp ops[] = {
        {"add", add},
        {"sub", sub},
        {"mul", mul}
    };
    for (int i = 0; i < 3; i++) {
        printf("ops[%d] (%s): %d\n", i, ops[i].name, ops[i].func(6, 3));
    }

    // Array of function pointers
    op_func func_array[3] = {add, sub, mul};
    int expected[] = {9, 3, 18};
    int arr_ok = 1;
    for (int i = 0; i < 3; i++) {
        int result = func_array[i](6, 3);
        printf("func_array[%d](6,3) = %d (expected: %d)\n", i, result, expected[i]);
        if (result != expected[i]) arr_ok = 0;
    }

    if (apply(add, 10, 3) == 13 && apply(sub, 10, 3) == 7 && arr_ok) {
        printf("RESULT: Function pointers PASSED\n");
    } else {
        printf("RESULT: Function pointers FAILED\n");
    }
    return 0;
}
```

```bash
$CCC test_function_pointers.c -o test_fp_ccc && ./test_fp_ccc
gcc test_function_pointers.c -o test_fp_gcc && ./test_fp_gcc

# Compare assembly for indirect calls
$CCC -S test_function_pointers.c -o test_fp_ccc.s
gcc -S test_function_pointers.c -o test_fp_gcc.s
grep -n "call \*" test_fp_ccc.s
grep -n "call \*" test_fp_gcc.s
```

All three patterns produced correct output from both compilers:

```
apply(add, 10, 3) = 13
apply(sub, 10, 3) = 7
ops[0] (add): 9, ops[1] (sub): 3, ops[2] (mul): 18
func_array[0](6,3) = 9, func_array[1](6,3) = 3, func_array[2](6,3) = 18
RESULT: Function pointers PASSED
```

I then looked at the assembly to see how each compiler handles the indirect calls:

```bash
grep -n "call \*" test_fp_ccc.s
# 48:    call *%r10
# 233:   call *%r10
# 300:   call *%r10

grep -n "call \*" test_fp_gcc.s
# (no output)
```

CCC generated 3 `call *%r10` instructions, one for each of the three test patterns (callback, struct member, array element). `call *%r10` is an indirect call: instead of jumping to a hardcoded address, the CPU reads the address from register `%r10` and jumps there. This is the expected way to implement function pointer calls, since the target isn't known at compile time.

GCC's unoptimized assembly had zero `call *` instructions. This doesn't mean GCC avoided indirect calls entirely. GCC resolves the function pointer into a register and calls it through a different code pattern. Both approaches are valid, but CCC's is more explicit about the indirection.

---

## Floating Point & IEEE 754

The tests so far used integers. Floating-point is a different beast. Computers represent decimal numbers in binary using the IEEE 754 standard, and the IEEE 754 standard defines special values like `NaN` (Not a Number), positive and negative infinity, and negative zero, each with specific rules.
For example, `NaN` is not equal to itself, and dividing by negative zero must return negative infinity. Beyond special values, there's also the fundamental issue that binary can't represent most decimal fractions exactly, so adding `0.001` a thousand times doesn't give you exactly `1.0`. Both of these are areas where compilers can diverge if they handle floating-point differently. I tested all of them.

```c
// test_float.c
#include <stdio.h>
#include <math.h>
#include <float.h>

int main() {
    double a = 0.1 + 0.2;
    printf("0.1 + 0.2 = %.20f\n", a);
    printf("0.1 + 0.2 == 0.3? %s\n", (a == 0.3) ? "yes" : "no");

    double pos_inf = 1.0 / 0.0;
    double neg_inf = -1.0 / 0.0;
    double nan_val = 0.0 / 0.0;
    double neg_zero = -0.0;

    printf("1.0/0.0  = %f\n", pos_inf);
    printf("-1.0/0.0 = %f\n", neg_inf);
    printf("0.0/0.0  = %f\n", nan_val);
    printf("-0.0     = %f\n", neg_zero);

    printf("NaN == NaN? %d (expected: 0)\n", nan_val == nan_val);
    printf("NaN < 1.0?  %d (expected: 0)\n", nan_val < 1.0);
    printf("NaN > 1.0?  %d (expected: 0)\n", nan_val > 1.0);

    printf("-0.0 == 0.0? %d (expected: 1)\n", neg_zero == 0.0);
    printf("1/-0.0 = %f (expected: -inf)\n", 1.0 / neg_zero);

    double subnormal = DBL_MIN / 2.0;
    printf("Subnormal: %e\n", subnormal);
    printf("Subnormal > 0? %d (expected: 1)\n", subnormal > 0.0);

    double sum = 0.0;
    for (int i = 0; i < 1000; i++) {
        sum += 0.001;
    }
    printf("1000 * 0.001 = %.15f (expected: ~1.0)\n", sum);

    int pass = 1;
    if (a == 0.3) pass = 0;
    if (nan_val == nan_val) pass = 0;
    if (neg_zero != 0.0) pass = 0;
    if (!(subnormal > 0.0)) pass = 0;

    printf("RESULT: Floating point %s\n", pass ? "PASSED" : "FAILED");
    return 0;
}
```

```bash
$CCC test_float.c -o test_float_ccc -lm && ./test_float_ccc
gcc test_float.c -o test_float_gcc -lm && ./test_float_gcc
diff <(./test_float_ccc) <(./test_float_gcc)
```

```
diff output: (empty, no differences)
```

Zero difference. Every edge case, every special value, every decimal digit, identical. `NaN == NaN` correctly returns `0`, `-0.0 == 0.0` correctly returns `1`, `1.0 / -0.0` returns `-inf`, and the accumulated floating-point drift after 1,000 additions of `0.001` matched to 15 decimal places.

Every value matched down to the last decimal digit. x86-64 CPUs have two ways to do floating-point math: the older x87 FPU (which uses 80-bit extended precision internally) and the newer SSE/SSE2 instructions (which use 64-bit double precision). If one compiler used x87 and the other used SSE, the extra precision in x87 could cause tiny differences in rounding, especially in the accumulation test.
The fact that the results were identical suggests both compilers are using the same instruction set for floating-point, most likely SSE2. This is also what you'd expect: the System V ABI on x86-64 requires floating-point arguments to be passed in SSE registers (`xmm0`, `xmm1`, etc.), so any compiler targeting this platform is naturally pushed toward using SSE for all floating-point operations.

---

## Variadic Functions

Functions like `printf` accept a variable number of arguments. C provides `stdarg.h` with macros (`va_start`, `va_arg`, `va_end`, `va_copy`) to write your own variadic functions. This is a non-trivial test for a compiler because on x86-64, the first 6 integer arguments are passed in registers and the rest go on the stack, so the compiler has to generate code that navigates both. I wrote custom variadic functions for integers, doubles, and mixed types, including a test that passes more than 6 integer arguments to force the stack-based path.

```c
// test_variadic.c
#include <stdio.h>
#include <stdarg.h>

int sum_ints(int count, ...) {
    va_list args;
    va_start(args, count);
    int total = 0;
    for (int i = 0; i < count; i++) {
        total += va_arg(args, int);
    }
    va_end(args);
    return total;
}

double sum_doubles(int count, ...) {
    va_list args;
    va_start(args, count);
    double total = 0.0;
    for (int i = 0; i < count; i++) {
        total += va_arg(args, double);
    }
    va_end(args);
    return total;
}

void print_mixed(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    while (*fmt) {
        switch (*fmt) {
            case 'i': printf("%d ", va_arg(args, int)); break;
            case 'd': printf("%.2f ", va_arg(args, double)); break;
            case 's': printf("%s ", va_arg(args, char*)); break;
            default:  printf("? ");
        }
        fmt++;
    }
    printf("\n");
    va_end(args);
}

int sum_twice(int count, ...) {
    va_list args1, args2;
    va_start(args1, count);
    va_copy(args2, args1);
    int sum1 = 0, sum2 = 0;
    for (int i = 0; i < count; i++) sum1 += va_arg(args1, int);
    for (int i = 0; i < count; i++) sum2 += va_arg(args2, int);
    va_end(args1);
    va_end(args2);
    return sum1 + sum2;
}

int main() {
    printf("sum_ints(1..5) = %d (expected: 15)\n", sum_ints(5, 1, 2, 3, 4, 5));
    printf("sum_doubles(1.1, 2.2, 3.3) = %.1f (expected: 6.6)\n", sum_doubles(3, 1.1, 2.2, 3.3));
    printf("print_mixed: ");
    print_mixed("ids", 42, 3.14, "hello");
    printf("sum_twice(10,20,30) = %d (expected: 120)\n", sum_twice(3, 10, 20, 30));
    printf("sum_ints(1..10) = %d (expected: 55)\n", sum_ints(10, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10));

    int pass = (sum_ints(5, 1, 2, 3, 4, 5) == 15 &&
                sum_twice(3, 10, 20, 30) == 120 &&
                sum_ints(10, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10) == 55);
    printf("RESULT: Variadic functions %s\n", pass ? "PASSED" : "FAILED");
    return 0;
}
```

```bash
$CCC test_variadic.c -o test_var_ccc && ./test_var_ccc
gcc test_variadic.c -o test_var_gcc && ./test_var_gcc
diff <(./test_var_ccc) <(./test_var_gcc)
```

```
diff output: (empty, no differences)
```

All passed. `va_copy` is the most telling part of this test. On x86-64, the System V ABI passes the first 6 integer arguments in registers and the first 8 floats in SSE registers, with the rest going on the stack. Because of this, `va_list` isn't a simple pointer into the stack. It's a struct containing offsets into a register save area, a pointer to the stack overflow area, and separate tracking for integer and float arguments. `va_copy` has to deep-copy this struct so that two independent cursors can iterate through the same arguments without interfering with each other. CCC got this right, including the mixed-type case where integer and float arguments are read from different register save areas.

---

## Switch Statements

A `switch` statement can be compiled in different ways. For a dense set of cases (0, 1, 2, ..., 19), a compiler can generate a jump table: an array of addresses indexed by the switch value, giving O(1) dispatch. For sparse cases (1, 50, 100, 500, 1000), a jump table would waste space, so the compiler typically uses a chain of comparisons or a binary search. There's also fall-through behavior, where omitting a `break` causes execution to cascade into the next case. I tested all three: a dense 20-case switch, a sparse switch, and fall-through.

```c
// test_switch.c
#include <stdio.h>

const char* day_name(int day) {
    switch (day) {
        case 0: return "Sunday";    case 1: return "Monday";
        case 2: return "Tuesday";   case 3: return "Wednesday";
        case 4: return "Thursday";  case 5: return "Friday";
        case 6: return "Saturday";  default: return "Unknown";
    }
}

int compute(int op) {
    switch (op) {
        case 0:  return 0;    case 1:  return 1;    case 2:  return 4;
        case 3:  return 9;    case 4:  return 16;   case 5:  return 25;
        case 6:  return 36;   case 7:  return 49;   case 8:  return 64;
        case 9:  return 81;   case 10: return 100;  case 11: return 121;
        case 12: return 144;  case 13: return 169;  case 14: return 196;
        case 15: return 225;  case 16: return 256;  case 17: return 289;
        case 18: return 324;  case 19: return 361;  default: return -1;
    }
}

int fallthrough_test(int x) {
    int result = 0;
    switch (x) {
        case 1: result += 1;    // fall through
        case 2: result += 10;   // fall through
        case 3: result += 100;  break;
        case 4: result += 1000; break;
        default: result = -1;
    }
    return result;
}

int main() {
    for (int i = 0; i <= 7; i++)
        printf("day_name(%d) = %s\n", i, day_name(i));

    int dense_ok = 1;
    for (int i = 0; i < 20; i++) {
        if (compute(i) != i * i) { dense_ok = 0; break; }
    }
    printf("Dense switch: %s\n", dense_ok ? "PASSED" : "FAILED");

    printf("fallthrough(1) = %d (expected: 111)\n", fallthrough_test(1));
    printf("fallthrough(2) = %d (expected: 110)\n", fallthrough_test(2));
    printf("fallthrough(3) = %d (expected: 100)\n", fallthrough_test(3));
    printf("fallthrough(4) = %d (expected: 1000)\n", fallthrough_test(4));

    int ft_ok = (fallthrough_test(1) == 111 && fallthrough_test(2) == 110 &&
                 fallthrough_test(3) == 100 && fallthrough_test(4) == 1000);
    printf("RESULT: Switch statement %s\n", (dense_ok && ft_ok) ? "PASSED" : "FAILED");
    return 0;
}
```

```bash
$CCC test_switch.c -o test_switch_ccc && ./test_switch_ccc
gcc -O2 test_switch.c -o test_switch_gcc && ./test_switch_gcc

# Assembly comparison
$CCC -S test_switch.c -o test_switch_ccc.s
gcc -O2 -S test_switch.c -o test_switch_gcc.s
grep -c "cmp\|je\|jne" test_switch_ccc.s
grep -c "cmp\|je\|jne" test_switch_gcc.s
grep "jmp \*" test_switch_ccc.s
grep "jmp \*" test_switch_gcc.s
```

All cases returned correct values, and CCC handled fall-through semantics correctly. In the assembly:

```
CCC: 24 comparison instructions, 3 indirect jumps (jmp *%rdx)
GCC: 21 comparison instructions, 0 indirect jumps
```

In the `compute` function, every case just returns `i * i` for the given input. GCC at `-O2` recognizes this pattern and replaces the entire switch with a lookup table: it stores the return values `{0, 1, 4, 9, 16, ...}` in an array in the `.rodata` section, then uses the input as an index to read the answer directly from memory. One memory read, no jumps, no comparisons. CCC takes a different approach: it builds a jump table (an array of code addresses), uses `jmp *%rdx` to jump to the right case block, and then executes the return from there. This is a valid implementation of a jump table and still O(1), but it involves an extra level of indirection compared to GCC's data-only approach.

---

## Volatile & Restrict

The `volatile` keyword tells the compiler that a variable's value can change at any time (e.g., a hardware register or a value modified by another thread), so the compiler must not optimize away reads or writes to it. If you write to a `volatile` variable five times in a row, all five writes must appear in the assembly, even if a normal optimization pass would collapse them into one. The `restrict` keyword is the opposite direction: it's a promise from the programmer that two pointers don't alias the same memory, which allows the compiler to optimize more aggressively. I tested both to see if CCC respects these semantics.

```c
// test_volatile_restrict.c
#include <stdio.h>

void test_volatile() {
    volatile int sensor = 0;
    sensor = 1; sensor = 2; sensor = 3; sensor = 4; sensor = 5;
    int a = sensor;
    int b = sensor;
    int c = sensor;
    printf("volatile reads: %d %d %d (all should be 5)\n", a, b, c);
}

void add_arrays_restrict(int * restrict dest, const int * restrict src1,
                          const int * restrict src2, int n) {
    for (int i = 0; i < n; i++) dest[i] = src1[i] + src2[i];
}

int main() {
    test_volatile();

    int a[] = {1, 2, 3, 4, 5};
    int b[] = {10, 20, 30, 40, 50};
    int c[5];
    add_arrays_restrict(c, a, b, 5);

    printf("restrict result: ");
    for (int i = 0; i < 5; i++) printf("%d ", c[i]);
    printf("(expected: 11 22 33 44 55)\n");

    int ok = 1;
    int expected[] = {11, 22, 33, 44, 55};
    for (int i = 0; i < 5; i++) if (c[i] != expected[i]) ok = 0;
    printf("RESULT: Volatile/Restrict %s\n", ok ? "PASSED" : "FAILED");
    return 0;
}
```

```bash
$CCC test_volatile_restrict.c -o test_vr_ccc && ./test_vr_ccc
gcc -O2 test_volatile_restrict.c -o test_vr_gcc && ./test_vr_gcc

# Check volatile writes are preserved in assembly
$CCC -S test_volatile_restrict.c -o test_vr_ccc.s
grep -A 50 "test_volatile:" test_vr_ccc.s | grep -c "mov.*rbp"
```

CCC accepts the `restrict` keyword and correctly compiles `volatile` variables. The volatile test with 5 sequential writes and 3 sequential reads produced expected output. In the assembly, I found 12 `mov` instructions touching `%rbp`, consistent with preserving all volatile accesses.

---

## C11 Conformance

The C language has evolved through several standards: C89, C99, C11, C17. Each one added features that require new compiler support. C11 in particular introduced `_Static_assert` (compile-time assertions), `_Generic` (type-based dispatch at compile time, similar to function overloading), designated initializers for structs and arrays, compound literals, anonymous structs/unions, and variable-length arrays (VLAs). Supporting these features requires more than just parsing: `_Generic` needs the compiler to resolve types during compilation, and VLAs need runtime stack allocation. I tested all of them.

```c
// test_c11.c
#include <stdio.h>
#include <string.h>

_Static_assert(sizeof(int) >= 4, "int must be at least 4 bytes");
_Static_assert(sizeof(char) == 1, "char must be 1 byte");

#define type_name(x) _Generic((x), \
    int: "int",                     \
    float: "float",                 \
    double: "double",               \
    char*: "char*",                 \
    default: "unknown"              \
)

struct Point { int x; int y; int z; };

struct Packet {
    int header;
    union {
        struct { int a; int b; };
        long combined;
    };
};

int main() {
    // Designated initializers
    struct Point p = {.z = 30, .x = 10};
    printf("Point: x=%d, y=%d, z=%d (expected: 10, 0, 30)\n", p.x, p.y, p.z);

    // Array designated initializers
    int arr[10] = {[3] = 30, [7] = 70};
    printf("arr[0]=%d, arr[3]=%d, arr[7]=%d (expected: 0, 30, 70)\n",
           arr[0], arr[3], arr[7]);

    // Compound literals
    struct Point *pp = &(struct Point){100, 200, 300};
    printf("Compound literal: %d, %d, %d (expected: 100, 200, 300)\n",
           pp->x, pp->y, pp->z);

    // _Generic
    int i = 42; float f = 3.14f; double d = 2.718; char *s = "hello";
    printf("type of i: %s (expected: int)\n", type_name(i));
    printf("type of f: %s (expected: float)\n", type_name(f));
    printf("type of d: %s (expected: double)\n", type_name(d));
    printf("type of s: %s (expected: char*)\n", type_name(s));

    // Anonymous struct/union
    struct Packet pkt;
    pkt.header = 1; pkt.a = 0x0000FFFF; pkt.b = 0x7FFF0000;
    printf("Packet: header=%d, a=0x%X, b=0x%X\n", pkt.header, pkt.a, pkt.b);

    // VLA
    int n = 5;
    int vla[n];
    for (int j = 0; j < n; j++) vla[j] = j * j;
    printf("VLA: ");
    for (int j = 0; j < n; j++) printf("%d ", vla[j]);
    printf("(expected: 0 1 4 9 16)\n");

    int pass = (p.x == 10 && p.y == 0 && p.z == 30 &&
                arr[3] == 30 && arr[7] == 70 &&
                pp->x == 100 && strcmp(type_name(i), "int") == 0);
    printf("RESULT: C11 conformance %s\n", pass ? "PASSED" : "FAILED");
    return 0;
}
```

```bash
$CCC test_c11.c -o test_c11_ccc && ./test_c11_ccc
gcc -std=c11 test_c11.c -o test_c11_gcc && ./test_c11_gcc
diff <(./test_c11_ccc) <(./test_c11_gcc)
```

All output matched GCC (`-std=c11`) exactly. `_Static_assert`, `_Generic`, designated initializers, compound literals, anonymous structs/unions, and VLAs, all working.

To make sure `_Static_assert` was genuinely being evaluated and not silently skipped, I also tested a failing assertion:

```c
// test_c11_negative.c
#include <stdio.h>

_Static_assert(sizeof(int) == 8, "int should not be 8 bytes on this platform");

int main() {
    printf("This should NOT compile.\n");
    return 0;
}
```

```
CCC: test_c11_negative.c:4:1: error: static assertion failed: int should not be 8 bytes on this platform
GCC: test_c11_negative.c:3:1: error: static assertion failed: "int should not be 8 bytes on this platform"
```

Both compilers rejected it with the correct error message. CCC is actually evaluating the assertion at compile time, not ignoring it. Given that CCC silently accepted four out of six broken programs in the diagnostics test, this was worth verifying.

`_Generic` is the most interesting one here. In our test, `type_name(i)` where `i` is an `int` needs to resolve to the string `"int"` at compile time. The compiler has to look at the expression passed to `_Generic`, determine its type, then match it against the list of type-value pairs (`int: "int"`, `float: "float"`, etc.) and substitute the correct one. This means the compiler needs a working type system that can resolve types during compilation, not just during code generation. The fact that CCC correctly distinguished `int`, `float`, `double`, and `char*` in our test shows that its type resolution infrastructure is solid.

---

## Compile Speed

All the tests so far focused on correctness and code quality. But compile speed matters too, especially in large codebases where developers run the compiler hundreds of times a day. A compiler that does fewer optimization passes should be faster, but how much faster? I generated a 7,513-line C file with 500 functions and timed CCC against GCC at two optimization levels:

```bash
# Generate a large file
for i in $(seq 1 500); do
    cat >> test_large.c << EOF
int func_${i}(int x) {
    int a = x + ${i};
    int b = a * 2;
    int c = b - ${i};
    int d = c / 2;
    if (d > 100) return d - 100;
    else if (d > 50) return d * 2;
    else return d + ${i};
    return 0;
}
EOF
done

# Time each compiler
time $CCC test_large.c -o test_large_ccc
time gcc test_large.c -o test_large_gcc
time gcc -O2 test_large.c -o test_large_gcc_o2

# Binary sizes
ls -la test_large_ccc test_large_gcc test_large_gcc_o2
```

| Compiler | Time | Binary Size |
|---|---|---|
| CCC | **0.315s** | 96,584 bytes |
| GCC (unoptimized) | 0.481s | 97,896 bytes |
| GCC `-O2` | 1.554s | 73,328 bytes |

CCC was **35% faster** than GCC unoptimized and **5x faster** than GCC at `-O2`. GCC at `-O2` produces a 25% smaller binary, which is consistent with it doing more work during compilation (optimization passes, dead code elimination, register allocation).

All three produced identical runtime output.

---

## Error Diagnostics

A compiler's job isn't just to compile valid code. It also needs to reject invalid code with useful error messages. Good diagnostics catch bugs early: type mismatches, wrong argument counts, duplicate definitions. If a compiler silently accepts broken code, the developer gets no signal that something is wrong until the program crashes at runtime, or worse, produces subtly incorrect results. I fed both compilers 6 intentionally broken programs to compare diagnostic quality.

```c
// test_error_9a.c -- Missing semicolon
int main() { int x = 5  return x; }

// test_error_9b.c -- Undeclared variable
int main() { return y; }

// test_error_9c.c -- Type mismatch
int main() { int x = "hello"; return x; }

// test_error_9d.c -- Too many arguments
int add(int a, int b) { return a + b; }
int main() { return add(1, 2, 3); }

// test_error_9e.c -- Missing return type
foo() { return 42; }
int main() { return foo(); }

// test_error_9f.c -- Duplicate definition
int x = 5;
int x = 10;
int main() { return x; }
```

```bash
for test in 9a 9b 9c 9d 9e 9f; do
    echo "=== Test $test ==="
    echo "--- CCC ---"
    $CCC test_error_${test}.c -o /dev/null 2>&1
    echo "--- GCC ---"
    gcc test_error_${test}.c -o /dev/null 2>&1
    echo ""
done
```

| Error Type | CCC | GCC |
|---|---|---|
| Missing semicolon | PASS - Caught, with fix-it hint | PASS - Caught |
| Undeclared variable | PASS - Caught | PASS - Caught |
| Type mismatch (`int x = "hello"`) | FAIL - Silent, compiled without warning | PASS - Warning |
| Too many arguments to function | FAIL - Silent, compiled without error | PASS - Error |
| Missing return type | FAIL - Silent, compiled without warning | PASS - Warning |
| Duplicate global definition | FAIL - Silent, compiled without error | PASS - Error |

CCC caught 2 out of 6. GCC caught all 6. For the four that CCC missed (type mismatches, wrong argument counts, missing return types, duplicate definitions), there was no warning, no error, nothing. The code compiled and produced a binary. In my opinion, that silence is the most dangerous outcome. A compiler that rejects valid code is annoying but safe. A compiler that accepts broken code gives the developer false confidence.

That false confidence is what turns a compile-time catch into a production incident. Code like `int x = "hello"` might happen to work on one platform because the pointer value fits in an `int`, but crash on another where pointer sizes differ. A wrong argument count might read garbage from the stack and produce incorrect results that only surface under specific inputs. These are the kind of bugs that pass all your tests locally, survive code review, and show up at 3 AM in a live system.

---

## Summary

| Test Area | CCC | GCC |
|---|---|---|
| Pointer arithmetic & alignment | PASS - Correct | PASS - Correct |
| Multi-file compilation & linking | PASS - Correct | PASS - Correct |
| Function pointers & indirect calls | PASS - Correct | PASS - Correct |
| Floating point & IEEE 754 | PASS - Identical to GCC | PASS - Correct |
| Preprocessor (macros, `#ifdef`) | PASS - Correct (verbose `-E` output) | PASS - Correct |
| Variadic functions (`va_list`, `va_copy`) | PASS - Correct | PASS - Correct |
| Constant folding | PASS - Pre-computes | PASS - Pre-computes |
| Switch (dense, sparse, fall-through) | PASS - Correct | PASS - Correct |
| Volatile / restrict | PASS - Correct | PASS - Correct |
| C11 (`_Generic`, VLA, designated init) | PASS - Full support | PASS - Full support |
| Dead code elimination | FAIL - Keeps unreachable code | PASS - Eliminates |
| Deep recursion (n=10M) | FAIL - Segfault | FAIL - Segfault (unopt), PASS - Survived at -O3 |
| Assembly efficiency | FAIL - Instruction inflation | PASS - Tight codegen |
| Error diagnostics | FAIL - 2/6 caught | PASS - 6/6 caught |
| Compile speed | PASS - 35% faster | Slower (doing more work) |
| Binary size | Comparable despite fewer features | PASS - 25% smaller at `-O2` |

CCC is semantically correct across a wide range of C features: memory layout, IEEE 754 floating point, variadic functions, C11 features including `_Generic`. For a compiler built by AI agents, the breadth of correct behavior is impressive.

But it's a literal translator. It takes C code and faithfully converts it to assembly, instruction by instruction, without the optimization passes that GCC has accumulated over decades. No dead code elimination for control flow, and a lot of unnecessary register spilling.

Out of the six broken programs, CCC caught only two. It silently compiled code with type mismatches, wrong argument counts, missing return types, and duplicate definitions. Combined with the dead code elimination and instruction inflation findings from earlier, a pattern emerges: CCC handles the core compilation pipeline (parsing C, generating assembly, producing a working binary) correctly. What it lacks is the layer of analysis that sits on top: optimization passes that make the output efficient, and diagnostic checks that catch mistakes before they become bugs. These are the areas where decades of work on GCC show.

Anthropic themselves acknowledge this in their blog post: "The generated code is not very efficient. Even with all optimizations enabled, it outputs less efficient code than GCC with all optimizations disabled." Our assembly inspection and binary size tests confirm exactly this.

## Sources

- [Building a C compiler with a team of parallel Claudes, Anthropic Engineering Blog](https://www.anthropic.com/engineering/building-c-compiler)
- [Claude's C Compiler, GitHub](https://github.com/anthropics/claudes-c-compiler)
