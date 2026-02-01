---
layout: post
title: "Optimizing a Kernel from 147,734 to 2,333 Cycles: A Learning Journey"
date: 2026-02-01
categories: [performance, optimization, kernel]
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2Fperformance%2Foptimization%2Fkernel%2F2026%2F02%2F01%2Fkernel-optimisation-learning-journey.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# Optimizing a Kernel from 147,734 to 2,333 Cycles: A Learning Journey

> **A note before you read**: Anthropic suggests not sharing solutions to their performance take home to avoid spoilers. I am sharing this because my score is far from their recruiting criteria, and this article is meant purely as a learning resource for folks who have no background in kernel optimization. If you are planning to attempt the challenge yourself, I would encourage you to try it first before reading this. The learning comes from the struggle, and this article will still be here when

When I encountered Anthropic's [performance optimization challenge](https://github.com/anthropics/original_performance_takehome), I had no idea what SIMD or VLIW meant. I had never optimized a kernel before. This article is what I wish someone had explained to me at the start of this journey.

What follows is not just the optimizations I made, but the understanding I built along the way. If you are reading this and feeling lost about vectorization or instruction level parallelism, you are exactly where I was before starting the assignment.

## Understanding the Problem

Before we delve into the optimizations, it helps to understand what the kernel actually does.

The challenge involves 256 workers traversing a binary tree for 16 rounds. At each step, a worker picks up a value from its current tree node, mixes it with its own value using a hash function, and then decides whether to go left or right based on that hash. If a worker reaches the bottom of the tree, it wraps back to the root.

The tree is a perfect binary tree with height 10. This means it has 2^11 minus 1 nodes, which equals 2047 nodes total. The nodes are numbered 0 through 2046, where node 0 is the root, nodes 1 and 2 are its children, nodes 3 through 6 are the next level, and so on.

The catch is that this runs on a simulated VLIW (Very Long Instruction Word) processor with strict constraints on what it can do each cycle:

- 12 scalar ALU operations (basic math on single values)
- 6 vector ALU operations (math on 8 values simultaneously)
- 2 memory loads
- 2 memory stores
- 1 control flow operation (branches, selects)

The key insight about VLIW is that all these limits apply simultaneously. In a single cycle, you can do 12 scalar operations AND 6 vector operations AND 2 loads AND 2 stores AND 1 control flow operation, as long as they do not depend on each other.

The baseline implementation took 147,734 cycles. My goal was to bring this down as much as possible.

## How I Approached This

I want to be upfront about my process. With no background in kernel optimization, I leaned heavily on LLMs for reading up on SIMD, VLIW, thinking through ideas, prompting and iterating on responses.

The journey looked something like this:

- Started lost, reading up on SIMD and VLIW architectures
- Pen and paper sketches trying to visualize instruction bundling
- Unrolling loops, merging computations across tree levels

The journey had two major phases, each building on insights from the previous one.

## Phase 1: Learning to Think in Batches (4,485 cycles)

The first breakthrough came from understanding vectorization. Let me explain this concept because it is fundamental to everything that follows.

### What is Vectorization?

Imagine you need to add 1 to eight different numbers. You could do this one at a time, which takes eight operations. Or, if your processor supports it, you could pack all eight numbers into a vector and add 1 to all of them in a single operation.

This processor supports vectors of length 8 (VLEN = 8 in the code). So instead of processing 256 workers one by one, I could group them into 32 batches of 8 workers each. Each batch gets processed with vector operations. One vector operation does the work of eight scalar operations, but it only uses one of the 6 vector slots per cycle.

This was my first major insight: process workers in batches of 8.

### Keeping Data in Scratch Memory

The second insight was about memory hierarchy. The processor has two types of memory:

- Main memory: where the tree values and worker data live
- Scratch memory: a fast, local storage area (think of it like registers)

Reading from main memory is slow and limited to 2 loads per cycle. Scratch memory is much faster. The strategy was to load all worker data into scratch memory at the start, keep it there while doing all 16 rounds of computation, and only write results back at the end.

This avoided repeated trips to main memory for the same data.

### Packing Multiple Operations Per Cycle

The third insight was about parallelism. Remember those per-cycle limits? They are separate execution units that can all work simultaneously.

Think of it like cooking. You can have something in the oven, something on the stove, and be chopping vegetables all at the same time. Similarly, while the vector unit is computing hash values, the memory unit can be loading the next batch of data.

I started organizing the code to keep multiple units busy in the same cycle. Here is a simple example from the hash computation:

```python
# Process 3 batches worth of hash operations in one cycle (uses 6 VALU slots)
for start in range(0, N_BATCH, 3):
    end = min(start + 3, N_BATCH)
    hash_ops = []
    for j in range(start, end):
        hash_ops.append((op1, v_t1[j], v_val[j], vc1))
        hash_ops.append((op3, v_t2[j], v_val[j], vc3))
    self.instrs.append({"valu": hash_ops})
```

Each batch contributes 2 vector operations (one for each part of the hash stage), and we can fit 3 batches into the 6 VALU slots available per cycle.

### Round 0 Optimization

There was one more optimization in this phase. In round 0, all 256 workers start at node 0 (the root). They all need the same value. Instead of doing 32 separate loads (one per batch), I could load the root value once and broadcast it to all batches.

After these changes, the code ran in 4,485 cycles. That is about 33 times faster than the baseline. I was happy with this, but I kept wondering if there was more to squeeze out.

## Phase 2: Understanding the Structure (2,333 cycles)

The jump from 4,485 to 2,333 cycles came from a deeper understanding of the problem structure. Let me walk through the key insights.

### The Tree Has Predictable Patterns

Here is something I had not fully appreciated initially. The tree traversal follows a predictable pattern based on depth.

At depth 0, everyone is at node 0. That is just 1 unique location.
At depth 1, workers can only be at node 1 or node 2. That is 2 unique locations.
At depth 2, workers can only be at nodes 3, 4, 5, or 6. That is 4 unique locations.
At depth 3 and beyond, the number of possible nodes doubles each level, and we need memory gathers.

The depth cycles through 0 to 10 repeatedly (since cycle length equals forest height plus 1, which is 11). With 16 rounds, we go through depths 0 through 10, then 0 through 4 again.

My backup version treated every round the same way: compute addresses for all 256 workers, then gather values from memory. But why gather from memory when you know there are only 1, 2, or 4 unique values needed?

I rewrote the code to have specialized logic for each early depth:

```python
if depth == 0:
    # Everyone needs the root value, just broadcast it
    for start in range(0, N_BATCH, 6):
        end = min(start + 6, N_BATCH)
        self.instrs.append({
            "valu": [("vbroadcast", v_nv[j], root_val) for j in range(start, end)]
        })

elif depth == 1:
    # Only 2 possible nodes (node 1 or node 2)
    # Preload both values, then select based on worker's index
    # Uses arithmetic: result = bit * (node2 - node1) + node1
    for start in range(0, N_BATCH, 6):
        end = min(start + 6, N_BATCH)
        self.instrs.append({
            "valu": [("==", v_t1[j], v_idx[j], v_two) for j in range(start, end)]
        })
    for start in range(0, N_BATCH, 6):
        end = min(start + 6, N_BATCH)
        self.instrs.append({
            "valu": [("multiply_add", v_nv[j], v_t1[j], v_diff_d1, v_node1) 
                     for j in range(start, end)]
        })
```

For depth 0, we broadcast. For depth 1, we preload both node values and use arithmetic to select between them. For depth 2, we use a vselect tree with 4 preloaded values. Only at depth 3 and beyond do we actually need memory gathers.

This saved a huge number of memory operations in the early rounds.

### Simplifying the Hash Function

The hash function has six stages. Each stage takes the current value, applies some operations, and produces a new value. The stages look like this:

```python
HASH_STAGES = [
    ("+", 0x7ED55D16, "+", "<<", 12),  # Stage 0
    ("^", 0xC761C23C, "^", ">>", 19),  # Stage 1
    ("+", 0x165667B1, "+", "<<", 5),   # Stage 2
    ("+", 0xD3A2646C, "^", "<<", 9),   # Stage 3
    ("+", 0xFD7046C5, "+", "<<", 3),   # Stage 4
    ("^", 0xB55A4F09, "^", ">>", 16),  # Stage 5
]
```

Each stage computes: `a = (a op1 const1) op2 (a op3 const2)`

I noticed that three of these stages (0, 2, and 4) have the pattern `a = (a + const) + (a << shift)`. Mathematically, this is equivalent to `a = a * (1 + 2^shift) + const`.

For example, stage 4 has shift = 3:
- Original: `a = (a + 0xFD7046C5) + (a << 3)`
- Simplified: `a = a * 9 + 0xFD7046C5`

The processor has a `multiply_add` instruction that computes `a * b + c` in one operation. By recognizing this pattern, I could reduce three operations to one for stages 0, 2, and 4.

### Recognizing When Computation is Unnecessary

Here is another insight. After each round, workers update their position using: `new_idx = 2 * idx + (bit + 1)`, where bit is 0 or 1 based on the hash.

At depth 10 (the leaf level), workers are at nodes 1023 through 2046. When they compute their next index using the formula, the result is always greater than 2047, which triggers a wrap back to 0.

So instead of computing the full formula and then checking for wraparound, I could just set `new_idx = 0` directly. No multiplication needed.

Similarly, at depth 0, all workers are at node 0, so the formula simplifies to `new_idx = 1 + bit`, which is either 1 or 2.

### Making Every Cycle Count with Instruction Merging

The final major optimization was about packing work more efficiently. The challenge is figuring out which operations can safely run in the same cycle.

Two operations can run together if they do not have data dependencies. For example, if operation A writes to a memory location and operation B reads from that same location, B must wait for A to finish. But if they use completely different memory locations, they can run simultaneously.

I wrote a scheduler that looks ahead at the next 95 operations and tries to pack as many independent operations into each cycle as possible. It tracks three types of dependencies:

- Read After Write (RAW): B reads what A writes
- Write After Read (WAR): B writes what A reads
- Write After Write (WAW): both write to the same location

The scheduler maintains sets of reads and writes for all skipped instructions, ensuring that any merged instruction does not violate these dependencies.

This was probably the single biggest improvement in phase 2. Instead of having cycles where only one or two execution units were busy, most cycles now had multiple units working in parallel.

### The Results

After all these optimizations, the code ran in 2,333 cycles. Starting from 147,734 cycles, that is a speedup of about 63 times.

## What I Learned

Looking back at this journey, a few lessons stand out.

First, you do not need to be an expert to tackle hard problems. I started with no knowledge of SIMD, VLIW, or kernel optimization. Having an LLM as a learning partner made a huge difference. It could explain concepts when I was confused, suggest approaches when I was stuck, and help debug when things broke. But the key was actively learning, not just copying code. I read documentation, sketched ideas on paper, and built my own understanding.

Second, the structure of your problem matters. Generic vectorization gave me a 33x speedup. Understanding the specific patterns in the tree traversal, recognizing which hash stages could be fused, and knowing when computation was unnecessary took me from 33x to 63x.

Third, keeping all parts of your processor busy is important. The processor can theoretically execute about 23 operations per cycle (12 ALU + 6 VALU + 2 load + 2 store + 1 flow). Most of my optimization work was about finding ways to fill those slots.

Finally, sometimes the best optimization is not doing the work at all. Setting the index to 0 instead of computing and wrapping. Selecting between preloaded values instead of loading from memory. These saved more cycles than making the existing operations faster.
