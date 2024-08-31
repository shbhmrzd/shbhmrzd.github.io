## Memory layout of a Rust program

Before we delve into the memory layout of a Rust program, it's essential to establish a foundational understanding of what a variable is, what it stores, and where this data resides in memory.

### Values and Variables

- **`Value`**: A combination of a type and an element of the type's domain values

- **`Variable`**:  A value can be stored on the stack, on the heap, or in other locations. A value commonly is stored in a 
variable, which is a named value slot on the stack.

- **`Pointer`**: A value that holds the address of a region of memory. A pointer can be dereferenced to access the 
value stored in the memory location it points to. 

```rust
let x = 42;
let y = 43;
let var1 = &x;
let mut var2 = &x; 
var2 = &y;
```
In the above code snippet, there are 
1. Four distinct values: 42 (an i32), 43 (an i32), the address of x (a pointer), and the address of y (a pointer)
2. Four variables: `x`, `y`, `var1`, `var2`. Later two variables hold values of pointer type.

Extending this understanding to `let greeting = "Hello world";`, we should recognize:
1. A string value "Hello world"
2. A variable `greeting` which holds a value of string type.

![](/assets/img/hello_world.png)

Even though you assign a string value to the variable `greeting`, the Rust compiler determines the type of `greeting` to be `&str`.
This means that greeting is a reference to a sequence of UTF-8 encoded bytes representing the string "Hello world".
String literals are stored in the program's binary in a read-only section of memory. 
The `&str` type represents a slice of this string data, which is immutable.

**But why use `&str` for String Literals?**

In Rust, when you create a variable and assign it a value, the compiler needs to determine where that value will be stored in memory.
There are two main options:
1. **Stack**: Values stored on the stack are allocated contiguously in memory and are deallocated in the reverse order they were allocated (more on this later). Stack allocation is fast and efficient, but the size of the value must be known at compile-time.
2. **Heap**: Values stored on the heap are dynamically allocated and can be of any size. The heap is managed by the operating system and can be slower than the stack due to the overhead of dynamic allocation and deallocation.
Now, let's consider what happens when we create a string literal in Rust:
```rust
let greeting = "Hello, world!";
```

In this case, the string literal `"Hello, world!"` is stored in the binary's read-only memory section. This memory is allocated by the compiler at compile-time and does not require any dynamic allocation.
When we create a variable greeting and assign it the string literal, the variable itself is allocated on the stack. However, instead of storing a copy of the string data, greeting holds a reference to the string literal in the binary's read-only memory. This reference is represented by the `&str` type, which consists of a pointer to the first character of the string and the length of the string.
Since `&str` is a reference, it does not require any additional memory allocation on the heap. The string data itself is already present in the binary's read-only memory, so &str can simply point to it without needing to copy or allocate any additional memory.


### Memory layout of a Rust program

Rust programs, like those in many compiled languages, are divided into distinct memory segments, each with a specific role in the program's execution. Understanding this layout helps to grasp how Rust manages memory safely and efficiently.

#### Text Segment (Code Segment):
- **Content**: This segment contains the compiled machine code of the program—the actual instructions that the CPU executes.
- **Characteristics**:
  - It's typically read-only to prevent modification of the code during execution.
  - Shared among processes in certain operating systems to save memory if the same program is run multiple times.

#### Data Segment:
- **Content**: This segment contains global and static variables that have been explicitly initialized by the programmer. These variables are mutable, meaning they can be modified at runtime.

```rust
static mut COUNTER: u32 = 0;
```
The `COUNTER` variable is initialized to 0 but can be modified later in the program, so it resides in the Initialized Data Segment.

- **Subsections**:
  - Initialized Data Segment: Holds global and static variables that are explicitly initialized before the program starts.
  - Uninitialized Data Segment (BSS - Block Started by Symbol): Holds global and static variables that are declared but not initialized. The operating system initializes them to zero before the program starts.

- **Characteristics**:
  - Data here is usually read-write.

#### Read-Only Data Segment (RODATA):
- **Content**: This segment contains constants and immutable data. This data is read-only, meaning it cannot be modified after the program starts.

```rust
const GREETING: &str = "Hello, world!";
static MAX_USERS: u32 = 100;
```
`GREETING` and `MAX_USERS` are immutable and cannot be modified during the program's execution, so they are placed in the RODATA segment.

- **Characteristics**:
  - It's typically used for storing string literals, numeric constants, and other immutable data.
  - Read-only to prevent modification during execution.
  - Often placed separately to enable certain optimizations and to avoid accidental modifications.

#### Heap:
- **Content**: The heap is a region of memory used for dynamic memory allocation. It is where memory is allocated at runtime using pointers, such as Box, Vec, or other heap-allocating types in Rust.
The heap typically "grows upward" in memory. This means that as more dynamic memory is allocated (e.g., through operations like Box::new, Vec::push, etc.), the heap expands toward higher memory addresses.
Example: If the heap starts at a lower memory address (say 0x1000...), when more memory is allocated on the heap, the heap pointer increases, moving to a higher address (e.g., from 0x1000... to 0x1001...).

- **Characteristics**:
  - Grows upward: The heap can grow towards higher memory addresses as more memory is allocated.
  - Memory allocated on the heap must be manually managed, but Rust’s ownership system helps ensure that memory is automatically deallocated when no longer needed.

#### Stack:
- **Content**: The stack is a region of memory used for storing local variables, function parameters, and return addresses. Each time a function is called, a new stack frame is pushed onto the stack.
The stack typically "grows downward" in memory. This means that as more data is pushed onto the stack (e.g., when functions are called and local variables are allocated), the stack expands toward lower memory addresses.
Example: If the stack starts at a high memory address (say 0x7fff...), when a new function is called, a stack frame is added, and the stack pointer decreases, moving to a lower address (e.g., from 0x7fff... to 0x7ffe...).
- **Characteristics**:
  - Grows downward: The stack grows towards lower memory addresses as new frames are pushed onto it.
  - Fast allocation/deallocation: Stack memory allocation is very fast because it only involves moving the stack pointer.
  - Limited size: The stack is generally much smaller than the heap, and overflowing the stack (e.g., via deep recursion) leads to a stack overflow.

#### Environment and Auxiliary Data:
- **Content**: This area stores data like environment variables, command-line arguments, and auxiliary information needed by the operating system.
- **Characteristics**:
  - Typically, allocated when the program starts.

```sql
+-------------------+ High Memory Addresses
|   Environment/    |
|   Auxiliary Data  | <- Command-line arguments, environment variables
+-------------------+
|       Stack       | <- Grows downward as new stack frames are added
|                   |
+-------------------+ 
|       Heap        | <- Grows upward as memory is dynamically allocated
|                   |
+-------------------+
|  Uninitialized    |
|  Data Segment     | (BSS)
+-------------------+
|  Initialized Data |<- Initialized static and global variables (part of Data Segment)
|      Segment      |
+-------------------+
|   Read-Only Data  | (RODATA)
|                   | <- Read-only data: constants, immutable static variables
+-------------------+
|   Text Segment    | <- Program code (read-only)
+-------------------+ Low Memory Addresses

```


**BSS in the Context of Rust:**

Rust enforces that all variables, including global and static ones, must be initialized before use. This means that the concept of "uninitialized" global or static variables, which would normally reside in the BSS segment in C/C++, doesn't directly apply in Rust.
Why the BSS Might Still Be Present:
- **Compiler and Linker Behavior**: Even though Rust enforces initialization, the compiler and linker still generate a standard memory
layout that includes the BSS segment because it's a common part of the memory model on most platforms. This ensures compatibility with the underlying system and tooling, which expect a BSS segment.
- **Zero-Initialization**: If Rust generates a global or static variable that is initialized to zero, the compiler might 
optimize this by placing it in the BSS segment, even though from Rust's perspective, the variable is "initialized" (to zero).
- **Portability and Compatibility**: Rust aims to be interoperable with other languages and existing systems.
Including a BSS segment in the memory layout helps ensure that Rust programs can run on a wide range of platforms without issues, and it aligns with standard practices in systems programming.

### References
[Rust for Rustaceans - Chapter 1 Foundations](https://rust-for-rustaceans.com/)

[Rust-Layout-and-Types](https://github.com/amindWalker/Rust-Layout-and-Types)

[Visualizing memory layout of Rust's data types](https://www.youtube.com/watch?v=7_o-YRxf_cc)