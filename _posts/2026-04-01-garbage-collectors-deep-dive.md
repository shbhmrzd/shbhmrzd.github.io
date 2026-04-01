---
layout: post
title: "Garbage Collection: From First Principles to Modern Collectors in Java, Go, and Python"
date: 2026-04-01
categories: [systems, garbage-collection, memory-management]
tags: [gc, java, go, python, mark-and-sweep, reference-counting, g1gc, zgc, memory-management]
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2Fsystems%2Fgarbage-collection%2Fmemory-management%2F2026%2F04%2F01%2Fgarbage-collectors-deep-dive.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# Garbage Collection: From First Principles to Modern Collectors in Java, Go, and Python

Over the last few years I have gone from Java to Go to Rust and now back to Java. The one thing that keeps coming up when
switching between these languages is garbage collection. Java and Go have it, Rust does not. In benchmarks, in latency discussions,
in "why is this service slow" conversations, GC is always somewhere in the picture. I kept hearing about GC pauses, throughput overhead,
and write barriers, but I did not completely understand what was happening underneath.


While looking for the origins I came across McCarthy's 1960 paper, which is famous for introducing Lisp but also happens
to be where mark-and-sweep was first described. That led me to Wilson's 1992 survey, "Uniprocessor Garbage Collection Techniques",
which organizes everything that followed into a clean taxonomy. Reading both made the modern collectors much easier to understand,
because G1GC, ZGC, Go's concurrent collector, and CPython's hybrid approach are all variations on ideas those papers describe.
I also wrote a toy GC in Go to see the mechanics for myself.

These are my notes from that process.

---

## The Papers That Started It

### McCarthy (1960): Recursive Functions of Symbolic Expressions and Their Computation by Machine

This paper is famous for introducing Lisp, but the garbage collector is buried in it almost as an implementation detail.
McCarthy needed a way to manage memory for symbolic expressions. Lisp programs manipulate lists of lists of lists, and the
recursive structure made it impractical to ask programmers to free memory manually. So he described a mechanism to do it automatically.

The mechanism is two phases. First, start from the root variables the program is actively using and traverse every object
they reference, flagging each one as reachable. Second, scan all of memory. Anything not flagged is garbage. Add it back to the free list.

That is mark-and-sweep. It handles cycles naturally (unreachable cycles never get flagged), requires no per-object bookkeeping,
and lets the programmer ignore memory entirely.

The cost was that the program had to stop completely while the collector ran. Every allocation, every computation, everything
froze until the mark and sweep finished. For the programs McCarthy was writing in 1960, this was perfectly reasonable. As programs grew
larger and moved into latency-sensitive environments like web servers handling thousands of requests per second, stopping the world became
a harder tradeoff to accept. Most of what modern GC research has produced is the answer to one question:

### Wilson (1992): Uniprocessor Garbage Collection Techniques

By 1992, thirty years of GC research had produced a lot of ideas but there wasn't much of shared vocabulary. Wilson's survey is the paper
that organized it all. It is not a new algorithm. It is a taxonomy that gives names and structure to ideas that were scattered across decades of papers.

Wilson formalizes the three classic algorithms that everything else is built on.

The first is mark-and-sweep, which is McCarthy's original algorithm. Start from the roots, walk the object graph, mark everything you can reach, then sweep through the heap and free anything unmarked. It handles cycles naturally and the implementation is straightforward. The downside is that after enough cycles of allocation and collection, the heap gets fragmented. Live objects end up scattered with small free gaps between them, and the allocator has to search harder to find space.

The second is copying, sometimes called semi-space. The idea is to split the heap into two halves. You allocate in one half, and when it fills up, you copy all the live objects into the other half and throw the first one away entirely. Fragmentation disappears because live objects get packed together during the copy. Allocation is fast because you just bump a pointer forward. The cost is that half your memory is always sitting empty, waiting to be the destination for the next copy.

The third is reference counting. Every object keeps a count of how many pointers point to it. When a new reference is created, the count goes up. When a reference is removed, it goes down. When it hits zero, the object is freed immediately. There is no tracing, no pause, and destruction is deterministic. The problem is cycles. If two objects point to each other, both have a count of at least 1, even when nothing else in the program can reach them. Neither will ever be freed by reference counting alone.


Beyond the three algorithms, Wilson explores two observations that modern collectors depend on.

The first is the **generational hypothesis**: most objects die young. In practice, the temporary objects a program allocates
(intermediate values, request-scoped buffers, loop variables) tend to become garbage very quickly, while a small fraction
of objects live for the entire program. If you collect young objects frequently and old objects rarely, you do most of
your work on the part of the heap that is mostly garbage, which is much cheaper than scanning everything every time.

The second is **tricolor marking**, an abstraction for incremental and concurrent collection. Instead of marking objects
as simply visited or unvisited, you use three colors: white (not yet seen), grey (seen but children not yet scanned), and
black (fully processed). The collector processes grey objects one at a time. At termination, white objects are garbage.
This abstraction is what makes it possible to run the collector and the application simultaneously without them corrupting
each other's view of the heap. Go's concurrent mark-and-sweep and ZGC's concurrent marking are both direct descendants of this idea.

Everything in the "Modern GCs" section of this article maps back to one of Wilson's categories. The engineering has gotten
much more sophisticated, but the underlying structure is the same.

---

## The Two Fundamental Approaches

Almost every garbage collector is either reference counting, tracing, or some combination of both. Wilson's paper is organized around this split, and it still holds thirty years later.

### Reference Counting

Each object maintains a count of how many references point to it. When a reference is created, the count goes up. When a reference is removed, it goes down. When it hits zero, the object is freed immediately.

```
Object A (refcount: 2)  <--- pointer from B
                         <--- pointer from C

C.ref = null   -->  Object A (refcount: 1)  // still alive
B.ref = null   -->  Object A (refcount: 0)  // freed immediately
```

This is what CPython uses as its primary mechanism. It is simple and gives you deterministic destruction. When the last reference to a file handle goes away, `__del__` runs and the file closes right there, not at some later GC cycle.

Two problems make reference counting insufficient on its own.

**Cycles.** If Object A points to Object B and Object B points back to A, both maintain a count of at least 1 even when nothing else in the program can reach them. Neither is ever freed.

```
  Object A (refcount: 1) ---> Object B (refcount: 1)
       ^                           |
       |___________________________|

  Nothing else points to A or B.
  Both are garbage, but refcount never hits 0.
```

This is not a theoretical edge case. Cycles show up naturally in linked data structures, parent-child relationships, observer patterns, and caches. I will talk about how Python deals with this when we get to CPython's GC later in the article.

**Per-mutation overhead.** Every pointer assignment requires updating reference counts. In a multithreaded program these must be atomic operations, which are significantly more expensive.
Every time you pass an object to a function, return it, or assign it to a field, you pay this cost.

### Tracing (Mark-and-Sweep)

Instead of tracking individual references, a tracing collector starts from a set of known-live references called the root set and traverses the entire object graph. Every object it can reach gets marked as alive. Everything else gets freed.

The root set is the starting point, so the definition of what counts as a root matters. The answer is the same across languages: a root is any reference the runtime can find without tracing. These are the pointers anchored to the program's execution state
right now, the things you know are alive before any traversal begins.

In practice, roots fall into a few categories.

Local variables and function arguments in every active stack frame are roots. The program is actively running those functions, so anything they reference is by definition in use.

Global and static variables are roots because they live for the entire lifetime of the program.

CPU registers are roots because when a JIT compiler optimizes a hot method, it may keep a frequently accessed object reference in a CPU register instead of writing it back to the stack. If the GC runs at that moment, the register holds the only live reference to that object. If the GC does not scan registers, it would free an object that is still in use. To prevent this, the runtime defines safe points in the code where GC can only occur, and at those points it snapshots the register state to find any references held there.

The runtime itself also holds roots that have nothing to do with user code. In the JVM, class loaders are roots: every class you load is referenced by its class loader, and as long as the class loader is alive, every class it loaded (including their static fields) stays alive. Interned strings are roots because `String.intern()` stores strings in a shared pool that the JVM maintains. JNI handles are roots because when native C or C++ code holds a reference to a Java object via the Java Native Interface, that reference lives outside the Java heap in a handle table that the GC must scan. Each live thread is a root, and its entire call stack of frames is part of the root set.

Go's runtime follows the same principle. Each goroutine has its own stack, and all goroutine stacks must be scanned to find roots. The runtime also tracks its own internal data structures, such as the finalizer queue, as part of the root set.

```
Stack frame (main)              Stack frame (handleRequest)
  conn   ------------------>  [Connection object] --> [Buffer]
  config ------------------>  [Config object]
                                request  ---------> [Request object]
                                response ---------> [Response object]

Everything reachable from these stack variables is alive.
Anything else on the heap is garbage.
```

The key insight is that roots are defined by what the runtime already knows is live without tracing. Everything else must earn its survival by being reachable from a root. This is why the concept is language-agnostic.
The specific set of roots differs between Java, Go, and Python, but the principle is the same: start from what you know is live, trace outward, and reclaim the rest.

Cycles are handled naturally. If A and B point to each other but neither is reachable from any root, the mark phase never visits them. They remain unmarked and get swept.

The cost: a naive mark-and-sweep must pause the entire program while it traces the heap. This stop-the-world pause was the defining problem of early garbage collectors and is what modern GCs have spent decades engineering around.

### Why Most Modern GCs Are Tracing-Based

Reference counting's per-mutation cost adds up in server workloads with high allocation rates. Every pointer write increments or decrements a count. In a multithreaded program those updates must be atomic, and atomic operations are expensive. At thousands of allocations per second across dozens of threads, that overhead becomes measurable.
The cycle problem requires a supplementary tracing pass anyway. And tracing collectors can be made concurrent, running alongside the application with only brief pauses.

Java and Go use tracing collectors. Python is the notable exception. It starts with reference counting and layers a tracing cycle detector on top.

---

## Tracing Variants

Wilson's paper describes four ways to implement tracing, each with different tradeoffs.

### Mark-Sweep

The simplest tracing collector. Two phases:

1. **Mark:** Starting from roots, traverse the object graph and set a mark bit on every reachable object.
2. **Sweep:** Walk through the entire heap. Any object without a mark bit is garbage. Free it and add the memory back to the free list.

```
Roots: [A, C]

Heap before marking:
  [A] --> [B] --> [D]
  [C] --> [E]
  [F]            (unreachable)
  [G] --> [H]    (unreachable)

After mark phase:
  [A*] --> [B*] --> [D*]     (* = marked/alive)
  [C*] --> [E*]
  [F]                         (not marked)
  [G] --> [H]                 (not marked)

Sweep phase: free F, G, H
```

The main problem with mark-sweep is fragmentation. After enough collection cycles, the heap looks like Swiss cheese: live objects scattered across it with small free gaps between them. You might have 100MB free in total but no single contiguous block large enough to satisfy a new allocation. The allocator has to maintain a free list and search it for a fit, which gets slower as the heap gets more fragmented.

### Copying (Semi-Space)

The heap is divided into two equal halves: from-space and to-space. Allocation happens in from-space using a simple bump pointer. When from-space fills up, the collector copies all live objects into to-space, updates all pointers, then swaps the roles.
The old from-space is discarded entirely.

```
From-space:  [A*][garbage][B*][garbage][C*]
To-space:    [empty........................]

After collection:
From-space:  [freed entirely................]
To-space:    [A*][B*][C*][free.............]
```

Allocation is extremely fast because it is just a pointer bump. Compaction happens naturally. The cost is that only half the heap is usable at any time.

### Mark-Compact

Same marking phase as mark-sweep, but instead of just freeing unmarked objects, the collector slides all live objects to one end of the heap. This eliminates fragmentation without the 50% memory overhead of copying collectors.

```
Before compaction:
  [A*][___][B*][___][___][C*][___][D*]

After compaction:
  [A*][B*][C*][D*][___][___][___][___]
                   ^
                   free pointer (bump allocation from here)
```

The downside is that compaction requires multiple passes over the heap: one to mark, one to compute new addresses, one to update all pointers, one to move objects.

### The Generational Hypothesis

One of the most influential observations in Wilson's paper is the weak generational hypothesis: most objects die young.

In a typical web server, each request creates temporary objects (parsers, intermediate strings, response builders) that live for milliseconds. Configuration objects, connection pools, and caches live for the entire application lifetime.

Generational collectors exploit this by dividing the heap into generations. New objects go into the young generation. If they survive a few collections, they get promoted to the old generation. Young generation collections are frequent and fast because most objects there are already dead. Old generation collections are rare.

```
+-------------------+---------------------+
|  Young Generation |   Old Generation    |
|  (collected often)|  (collected rarely) |
|                   |                     |
|  Eden | S0 | S1   |                     |
+-------------------+---------------------+
```

**Eden** is where all new objects are born. Every `new Object()` goes here. It fills up fast because most programs allocate at a high rate.

**S0 and S1** are two small survivor spaces. When Eden fills up and a minor GC runs, the collector copies every surviving object out of Eden into one of them, say S0. Next collection, survivors from both Eden and S0 get copied into S1. The one after that, back into S0. They alternate every cycle. This is the copying collector in action within the young generation: no fragmentation, no free list, just two halves that take turns being the destination. The cost is that you need two survivor spaces, but they are kept small because most objects in Eden are already dead by the time collection runs.

**Promotion to old generation.** After an object has bounced between S0 and S1 enough times (the default threshold in the JVM is 15 cycles), the collector decides it has earned its place and promotes it to the old generation. The old generation is collected much less frequently, and with a heavier algorithm (mark-compact rather than copying) because objects there are large and long-lived.

The key implementation challenge is tracking references from old to young objects. If an old object points to a young object, that young object must not be collected even if no young-generation root points to it. This is solved with a write barrier, a small piece of code injected at every pointer write that records cross-generational references in a remembered set.

---

## Building a Toy Mark-and-Sweep GC in Go

I wrote a minimal mark-and-sweep collector to make these concepts concrete. It is around 70 lines and demonstrates the full cycle: allocating objects, building an object graph, marking from roots, and sweeping unreachable objects.

```go
package main

import "fmt"

// Object represents a heap-allocated object.
type Object struct {
	name     string
	marked   bool
	children []*Object
}

// VM is a tiny virtual machine with a garbage collector.
type VM struct {
	heap  []*Object
	roots []*Object // simulates stack variables and globals
}

// NewObject allocates an object on the VM's heap.
func (vm *VM) NewObject(name string) *Object {
	obj := &Object{name: name}
	vm.heap = append(vm.heap, obj)
	return obj
}

// mark walks from every root and marks all reachable objects.
func (vm *VM) mark() {
	for _, root := range vm.roots {
		vm.markObject(root)
	}
}

func (vm *VM) markObject(obj *Object) {
	if obj == nil || obj.marked {
		return
	}
	obj.marked = true
	for _, child := range obj.children {
		vm.markObject(child)
	}
}

// sweep frees unmarked objects and resets marks on survivors.
func (vm *VM) sweep() {
	alive := []*Object{}
	for _, obj := range vm.heap {
		if obj.marked {
			obj.marked = false // reset for next GC cycle
			alive = append(alive, obj)
		} else {
			fmt.Printf("  collected: %s\n", obj.name)
		}
	}
	vm.heap = alive
}

// GC runs a full mark-and-sweep collection.
func (vm *VM) GC() {
	fmt.Printf("gc: heap has %d objects\n", len(vm.heap))
	vm.mark()
	vm.sweep()
	fmt.Printf("gc: %d objects remain\n\n", len(vm.heap))
}

func main() {
	vm := &VM{}

	a := vm.NewObject("A")
	b := vm.NewObject("B")
	c := vm.NewObject("C")
	_ = vm.NewObject("D") // allocated but never linked to anything

	// Build a graph: A -> B -> C
	a.children = append(a.children, b)
	b.children = append(b.children, c)

	// Only A is a root
	vm.roots = append(vm.roots, a)

	fmt.Println("=== GC #1: D is unreachable ===")
	vm.GC()

	// Create a cycle: C -> A, then remove all roots
	c.children = append(c.children, a)
	vm.roots = nil

	fmt.Println("=== GC #2: A->B->C->A cycle, no roots ===")
	vm.GC()
}
```

Running this:

```plaintext
=== GC #1: D is unreachable ===
gc: heap has 4 objects
  collected: D
gc: 3 objects remain

=== GC #2: A->B->C->A cycle, no roots ===
gc: heap has 3 objects
  collected: A
  collected: B
  collected: C
gc: 0 objects remain
```

First collection: A, B, and C are reachable through root A. D has no path from any root, so it gets collected.

Second collection: A, B, and C form a cycle (A->B->C->A), but there are no roots. The mark phase never visits any of them. All three get swept. This is exactly the scenario that defeats reference counting. Each object in the cycle has a non-zero reference count, but none are reachable from a root.

**Tracing GCs do not care about cycles. They only care about reachability from roots.**

One thing to note: the `markObject` function uses recursion, which would blow the stack on a deep object graph. A real garbage collector uses an explicit worklist instead of the call stack.

---
## Modern GCs in Practice

The toy collector above stops the world for the entire mark and sweep. Modern GCs have evolved to do most of their work concurrently while the application keeps running.

### Go: Tri-Color Concurrent Mark-and-Sweep

Go's garbage collector is non-generational, non-compacting, and concurrent. It does not separate objects by age, and it does not move objects in memory. The focus is on keeping pause times low.

The collector uses a tri-color abstraction for concurrent marking. Every object is in one of three states:

```
During marking:
  White --(collector discovers it)--> Grey --(all children scanned)--> Black

After marking ends:
  Black --> alive, retained
  White --> garbage, reclaimed by sweep
  (no Grey objects should remain at end of marking)
```

- **White**: not yet visited. Anything still white at the end of marking is garbage.
- **Grey**: visited, but its children have not all been scanned yet. The frontier of the traversal.
- **Black**: visited, all children scanned. Definitely alive.

The collector starts by coloring everything white, then greys the roots and processes grey objects until none remain. Everything still white gets swept.

```
Start: all objects white, roots grey

Step 1: Pick a grey object, scan its children
        - Mark children grey
        - Mark the scanned object black

Step 2: Repeat until no grey objects remain

Step 3: All white objects are garbage

Example:

  Roots: [A]

  Start:     A(grey) --> B(white) --> D(white)
             A(grey) --> C(white)

  Scan A:    A(black) --> B(grey) --> D(white)
             A(black) --> C(grey)

  Scan B:    A(black) --> B(black) --> D(grey)
             A(black) --> C(grey)

  Scan C:    A(black) --> B(black) --> D(grey)
             A(black) --> C(black)

  Scan D:    A(black) --> B(black) --> D(black)
             A(black) --> C(black)

  Result: any remaining white objects are garbage and get freed
```

The hard part is that the application keeps running and modifying pointers while the collector is traversing. If the application stores a pointer to a white object into a black object, the collector might never see it since it considers black objects done. That white object would be incorrectly freed.

Go solves this with a **hybrid write barrier**, introduced in Go 1.8. To understand why it works, it helps to look at the two older barriers it combines.

**Dijkstra's insertion barrier (1978)** says: whenever a pointer is written into an object, grey the new referent. If a black object stores a reference to a white object, the white object gets greyed before the collector can miss it. This preserves the tricolor invariant: no black object ever points directly to a white one.

The problem is that goroutine stacks are different from heap objects. The write barrier is injected by the compiler at heap pointer writes, things like writing into a struct field or a slice element. Stack writes are local variable assignments, and the compiler treats them separately. Putting a barrier on every local variable assignment would make function calls and basic operations significantly more expensive, so the barrier does not cover them. This means that during concurrent marking, a goroutine can freely write a pointer to a white object into a local variable, and no barrier fires. The collector has no idea this happened.

To fix this, at the end of concurrent marking, Go had to stop the world and re-scan every goroutine's entire stack from scratch. Any pointer to a white object found during re-scanning would get greyed, preventing it from being incorrectly freed. The pause time for this step scaled with the number of goroutines and the size of their stacks. A program with tens of thousands of goroutines could see multi-millisecond STW pauses just for this re-scan, even after the rest of the collector had been made concurrent. This was the dominant STW pause in Go before 1.8.

**Yuasa's deletion barrier (1990)** takes the opposite approach: whenever a pointer is about to be overwritten, grey the old referent before it disappears. This ensures anything that was reachable at the start of marking stays reachable through to the end, even if the application drops its reference during marking. The downside is that some objects that died during marking survive to the next cycle (floating garbage), because the barrier conservatively kept them alive.

**Go's hybrid barrier** combines both. On heap writes, it applies both barriers: it greys the old referent (Yuasa) and greys the new referent (Dijkstra). On stack writes, no barrier runs, but newly allocated objects on the stack start black rather than white. The combination gives the collector a strong enough invariant that it never needs to re-scan stacks at the end of marking. The STW pause to finalize marking dropped from tens of milliseconds to under a millisecond.

```
// What the hybrid barrier does on a heap pointer write:
// *slot = new_ptr

shade(*slot)   // grey the old referent (Yuasa: don't lose what was there)
shade(new_ptr) // grey the new referent (Dijkstra: don't miss what's arriving)
*slot = new_ptr
```

This is the throughput cost of concurrent collection: every heap pointer write during the mark phase runs this shade logic. The overhead is small per operation but adds up at high allocation rates. The tradeoff is that you get sub-millisecond STW pauses instead of tens-of-millisecond ones.

Go only stops the world briefly to scan goroutine stacks and toggle the write barrier on and off. The actual marking and sweeping happen concurrently with the application.

**No compaction.** Go does not move objects after allocation. Instead, Go uses a tcmalloc-style allocator that divides memory into size classes and allocates from per-processor caches. Objects are grouped into fixed size classes (8 bytes, 16 bytes, 32 bytes, up to 32 KB). Allocation picks an appropriately sized slot from a free list. This reduces fragmentation without needing to move objects, but does not eliminate it entirely.

**No generational collection.** The Go team's reasoning is that generational GC adds complexity (write barriers to track old-to-young pointers, promotion logic, generation size tuning) for uncertain benefit given Go's typical allocation patterns with goroutines and concurrent workloads. Go compensates by making its concurrent marker fast enough that the extra collection frequency is acceptable.

**Key milestones:**
- Go 1.5 (2015): Introduced concurrent GC. Before this, Go had a full stop-the-world collector with pauses of 10-100ms or more. This was the release that made Go viable for latency-sensitive services.
- Go 1.8 (2017): Hybrid write barrier. Reduced the overhead of maintaining the tricolor invariant during concurrent marking.
- Go 1.19 (2022): `GOMEMLIMIT`. Enabled Go programs to work within memory budgets in container environments.

**The GOGC knob.** Go exposes one primary tuning parameter: `GOGC`. It controls how much the heap can grow before the next GC cycle triggers. The default is 100, meaning GC triggers when the heap has doubled since the last collection.

```
GOGC=100 (default):
  After GC, live heap = 500MB
  Next GC triggers at: 500MB * (1 + 100/100) = 1000MB

GOGC=50 (more aggressive):
  After GC, live heap = 500MB
  Next GC triggers at: 500MB * (1 + 50/100) = 750MB

GOGC=200 (less aggressive):
  After GC, live heap = 500MB
  Next GC triggers at: 500MB * (1 + 200/100) = 1500MB
```

Lower `GOGC` means more frequent collection (lower memory usage, higher CPU overhead). Higher `GOGC` means less frequent collection (higher memory usage, lower CPU overhead).

Go 1.19 added `GOMEMLIMIT`, a soft memory limit. In container environments where you have a hard memory budget, `GOMEMLIMIT` tells the GC pacer to get more aggressive as memory usage approaches the limit.

**Try it yourself:**

```go
package main

import (
	"fmt"
	"runtime"
	"time"
)

var longLived []*[1024 * 1024]byte

func main() {
	fmt.Println("Go version:", runtime.Version())

	for round := 0; round < 50; round++ {
		// Short-lived: allocate small objects, let them die
		for i := 0; i < 5000; i++ {
			_ = make([]byte, 1024)
		}

		// Long-lived: retain every 10th round
		if round%10 == 0 {
			arr := new([1024 * 1024]byte)
			longLived = append(longLived, arr)
		}

		time.Sleep(50 * time.Millisecond)
	}

	var stats runtime.MemStats
	runtime.ReadMemStats(&stats)
	fmt.Printf("Total GC cycles: %d\n", stats.NumGC)
	fmt.Printf("Total STW pause: %v\n", time.Duration(stats.PauseTotalNs))
	fmt.Printf("Long-lived objects: %d\n", len(longLived))
}
```

Run with GC tracing enabled:

```bash
GODEBUG=gctrace=1 go run gcdemo.go
```

What to look for:

```
gc 1 @0.011s 1%: 0.044+0.56+0.13 ms clock, 0.62+0.21/0.57/0+1.8 ms cpu, 3->4->0 MB, 4 MB goal, 0 MB stacks, 0 MB globals, 14 P
```

Reading this left to right:
- `gc 1`: GC cycle number
- `@0.011s`: Time since program start
- `1%`: Percentage of CPU spent on GC so far
- `0.044+0.56+0.13 ms clock`: Three phases of the GC cycle: STW mark start (0.044ms) + concurrent mark and scan (0.56ms) + STW mark end (0.13ms)
  The STW pauses are the first and third numbers in the clock field. In this example, the total wall clock time the application was frozen is 0.044 + 0.13 = 0.174ms.
  The 0.56ms in the middle is concurrent: your application was running the whole time. In Go, STW pauses are typically under 1ms, often well under 0.1ms.

- `0.62+0.21/0.57/0+1.8 ms cpu`: CPU time breakdown. The format is: `STW-start + assist/background/idle + STW-end`. Each number means:

  ```
  0.62  +  0.21 / 0.57 / 0  +  1.8   ms cpu
  |         |      |      |      |
  STW       |   background idle  STW
  mark    assist  GC       GC    mark
  start   time    workers  time  end
  ```

  - `0.62ms` — CPU time across all cores for STW mark start. Higher than the wall clock (0.044ms) because Go parallelises the initial stack scan across multiple cores.
  - `0.21ms` — CPU time spent by application goroutines doing mutator assists. When a goroutine allocates faster than the GC can keep up, it is taxed and must do some marking work itself before its allocation is allowed.
  - `0.57ms` — CPU time used by dedicated background GC worker goroutines doing the concurrent marking.
  - `0` — CPU time by idle GC workers (goroutines that only pick up GC work when the scheduler has nothing else to run). Zero here means the dedicated workers handled everything.
  - `1.8ms` — CPU time across all cores for STW mark end. Higher than wall clock (0.13ms) because multiple cores work in parallel to drain remaining work and disable the write barrier.

  CPU time can exceed wall clock time when multiple cores work in parallel. CPU time for the concurrent phase can be less than wall clock because the GC shares cores with your application.
- `3->4->0 MB`: Heap size at GC start, heap size at GC trigger point, live heap after GC completes
- `4 MB goal`: Target heap size before the next GC triggers (based on GOGC and current live heap)
- `0 MB stacks`: Memory used by goroutine stacks
- `0 MB globals`: Memory used by global variables scanned during marking
- `14 P`: Number of logical processors (GOMAXPROCS)



---

### Java: G1GC (Garbage First Collector)

G1GC has been the default Java garbage collector since JDK 9. It is a generational, region-based collector. It traces, marks, and compacts, but does so incrementally rather than all at once.

**Region layout.** G1 divides the heap into equal-sized regions, typically 1MB to 32MB each depending on heap size. Each region plays one of four roles at any time: Eden, Survivor, Old, or Humongous (for objects larger than half a region). The role of a region can change between collections.

```
+-------+-------+-------+-------+-------+-------+-------+-------+
|  Eden | Eden  | Surv  |  Old  |  Old  | Hum   | Eden  | Free  |
+-------+-------+-------+-------+-------+-------+-------+-------+
| Eden  |  Old  |  Old  | Free  | Eden  | Surv  |  Old  |  Old  |
+-------+-------+-------+-------+-------+-------+-------+-------+

Each cell is one region. Roles change after each collection.
```

**Young collection (minor GC).** Eden regions fill up. G1 stops the world, marks live objects in Eden and Survivor regions using a parallel multi-threaded marker, copies survivors into new Survivor regions or promotes them to Old regions, and discards the old Eden regions entirely. This is a parallel stop-the-world pause, but it is short because young regions are small and young objects are mostly dead.

**Mixed collection.** Periodically, G1 runs a concurrent marking cycle to figure out which Old regions have the most garbage. Then it runs mixed collections: evacuating both young regions and the most profitable Old regions at the same time. This is where the "Garbage First" name comes from. G1 always picks the Old regions with the highest garbage density first, maximizing reclamation per unit of pause time.

**SATB (Snapshot-At-The-Beginning).** During concurrent marking, the application keeps running and modifying the object graph. G1 uses SATB to maintain correctness. At the start of marking, G1 takes a logical snapshot of which objects are live. Any objects that were live at that snapshot are treated as live for this cycle, even if the application discards them during marking. The write barrier records the pre-write values of modified fields into SATB queues. This is conservative (some garbage survives to the next cycle) but correct.

```
Concurrent marking is running. Application executes:
  obj.field = null   (was pointing to X)

Without SATB: X might have no other references, go unmarked, get freed while still in use.
With SATB:    Write barrier records "X was here before", marks X grey. Safe.
```

**Pause target.** You can configure G1's target max pause time with `-XX:MaxGCPauseMillis`. The default is 200ms. G1 tries to keep pauses within this target by adjusting region count, collection set size, and timing. It will not always succeed, particularly during Full GC, but it is the primary tuning knob.

**Try it yourself:**

```java
import java.util.ArrayList;
import java.util.List;

public class GCDemo {
  static List<byte[]> longLived = new ArrayList<>();

  public static void main(String[] args) throws InterruptedException {
    System.out.println("Starting GC demo...");

    for (int round = 0; round < 50; round++) {
      // Short-lived objects: create and immediately drop
      for (int i = 0; i < 1000; i++) {
        byte[] tmp = new byte[10 * 1024]; // 10KB each
      }

      // Long-lived: retain some objects to build up old gen
      if (round % 5 == 0) {
        longLived.add(new byte[1024 * 1024]); // 1MB
      }

      Thread.sleep(50);
    }

    System.out.println("Done. Long-lived objects: " + longLived.size());
  }
}
```

Run with G1GC logs:

```bash
# Compile
javac GCDemo.java

# Run with G1GC (default in Java 9+) and GC logging
java -Xmx256m \
     -XX:+UseG1GC \
     "-Xlog:gc*:file=gc_g1.log:time,uptime,level,tags" \
     GCDemo

# Or, for a concise one-liner output
java -Xmx256m -Xlog:gc GCDemo
```

What to look for in the log:

```
[0.005s][info][gc] Using G1
[0.135s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) 26M->3M(256M) 0.644ms
[0.812s][info][gc] GC(1) Pause Young (Normal) (G1 Evacuation Pause) 132M->7M(256M) 0.707ms
[1.710s][info][gc] GC(2) Pause Young (Normal) (G1 Evacuation Pause) 165M->13M(256M) 1.019ms
[2.528s][info][gc] GC(3) Pause Young (Normal) (G1 Evacuation Pause) 171M->19M(256M) 0.964ms
```

Reading this:
- `Using G1`: Confirms G1GC is the active collector
- `Pause Young (Normal)`: A minor GC collecting Eden and Survivor regions
- `G1 Evacuation Pause`: G1 is copying live objects out of collected regions into new ones
- `26M->3M(256M) 0.644ms`: Heap was 26MB before, 3MB after, total heap capacity 256MB, pause took 0.644ms
- Four GC cycles across 2.5 seconds of runtime, each completing in under 1.1ms. Most of the allocated objects were short-lived and collected in the young generation

---

### Java: ZGC (Z Garbage Collector)

ZGC is available since Java 11 and became production-ready in Java 15. Generational ZGC, which extends it with generational collection, arrived in Java 21. ZGC targets sub-millisecond pause times regardless of heap size, including heaps of hundreds of gigabytes.

G1 has short pauses for young collections but longer pauses during concurrent mark setup and mixed GC as the heap grows. ZGC's approach is different: it does almost everything (marking, relocation, reference processing) concurrently, keeping stop-the-world work to a minimum.

**Colored pointers.** ZGC encodes GC metadata directly in pointer bits. On a 64-bit platform, a pointer is 64 bits wide, but you do not actually need all 64 bits to address memory. 2^42 gives you 4TB of addressable space, which is more than most applications will ever use. That leaves over 20 bits sitting unused in every single pointer. ZGC repurposes a few of those spare bits to store GC state right inside the pointer itself.

```
64-bit pointer layout in ZGC:
+---------+--+--+--+--+--------------------------+
| unused  |F |M1|M0|R |     address (42 bits)    |
|  bits   |  |  |  |  |                          |
+---------+--+--+--+--+--------------------------+
```

Each metadata bit has a specific purpose:

- **M0 and M1 (mark bits):** These track whether the object has been marked alive. ZGC alternates between M0 and M1 each GC cycle. In cycle 1, the collector sets M0 on every reachable object. In cycle 2, it uses M1 instead. This way the collector can distinguish "marked this cycle" from "marked last cycle" without needing to clear all mark bits between cycles.

- **Remap (R):** This bit tracks whether the pointer has been updated after an object was relocated. During concurrent relocation, ZGC moves objects to new addresses but does not immediately update every pointer in the heap. Instead, it leaves the old pointers in place with the remap bit unset. When the application loads one of these stale pointers, the load barrier notices the unset remap bit and fixes it up.

- **Finalizable (F):** Indicates the object has a finalizer that needs to run before the object can be freed.

The clever part is that this metadata travels with the pointer. The GC does not need a separate side table to look up an object's GC state. Every pointer already carries it.

**Load barriers.** Every time the application loads a reference from the heap, ZGC inserts a load barrier. The barrier checks the pointer's color bits and takes action if they are not in the expected state.

Here is what this looks like in practice. Say the collector relocated an object from address `0x1000` to `0x2000` during a concurrent relocation phase. The application still has a pointer that says `0x1000` with the remap bit unset.

```
Application code:
  Object x = obj.field;

What actually executes:
  raw_ptr = load obj.field           // raw_ptr = 0x1000, remap bit = 0
  if (raw_ptr.color != expected) {   // remap bit is 0, expected is 1 → slow path
      new_addr = forwarding_table[0x1000]  // look up: object moved to 0x2000
      raw_ptr = set_address(raw_ptr, 0x2000)
      raw_ptr = set_remap_bit(raw_ptr)
      obj.field = raw_ptr            // fix the pointer in place for next time
  }
  x = raw_ptr                       // x now points to 0x2000
```

The next time any thread loads `obj.field`, the remap bit is already set. The barrier check passes on the fast path and there is no extra work. The stale pointer was fixed lazily on first access.

This is the key mechanism. Instead of the GC stopping the world to update every pointer to a relocated object all at once (like G1 does during evacuation), ZGC lets the application fix up pointers one at a time as it encounters them. The tradeoff is that every pointer load pays the cost of the barrier check, even when nothing was relocated. In practice the fast path (checking a few bits) is cheap enough that the overhead is small compared to the benefit of avoiding STW relocation pauses.

**Concurrent relocation.** G1 stops the world to evacuate objects out of collected regions. ZGC relocates objects while the application runs. It can do this because the load barrier handles the pointer fixup. There is a brief STW pause to start and end each phase (mark start, mark end, relocate start), but these are typically well under 1ms. The actual work of copying objects and fixing pointers happens concurrently.

**Generational ZGC (Java 21+).** The original ZGC did not partition the heap by age. Generational ZGC adds young and old generations while preserving the sub-millisecond pause guarantees. It collects young regions more frequently (where most garbage is) and old regions less frequently. The load barrier and colored pointer machinery is extended to handle the generational write barrier as well.

**When to use ZGC vs G1:**

| Scenario | Recommendation |
|---|---|
| Heap under 8GB, typical web service | G1GC default is fine |
| Heap over 8GB, latency-sensitive | ZGC |
| Occasional pause spikes are acceptable | G1GC |
| Sub-millisecond pauses required | ZGC |
| Java 21+ with latency requirements | Generational ZGC |

**Try it yourself:**

```bash
# Run with ZGC
java -Xmx256m \
     -XX:+UseZGC \
     "-Xlog:gc*:file=gc_zgc.log:time,uptime,level,tags" \
     GCDemo

# With generational ZGC (Java 21+)
java -Xmx256m \
     -XX:+UseZGC -XX:+ZGenerational \
     -Xlog:gc \
     GCDemo
```

What to look for:

```
[0.318s] GC(0) Garbage Collection (Warmup) 28M(11%)->12M(5%)
[0.321s] GC(0) Pause Mark Start 0.023ms
[0.489s] GC(0) Concurrent Mark 168.123ms
[0.491s] GC(0) Pause Mark End 0.019ms
[0.492s] GC(0) Concurrent Select Relocation Set 1.234ms
[0.502s] GC(0) Concurrent Relocate 10.456ms
```

The STW pauses are the lines labeled "Pause". Everything else is concurrent. Compare the pause durations here with the G1 output.

---

### Python: Reference Counting Plus Cyclic GC

CPython (the reference implementation of Python) is the main exception to the "tracing collectors dominate" pattern. It uses reference counting as the primary mechanism and layers a supplementary tracing cycle detector on top.

**Reference counting in CPython.** Every Python object has an `ob_refcnt` field. Python's C API increments this on `Py_INCREF` and decrements on `Py_DECREF`. When the count hits zero, the object is freed immediately in `_Py_Dealloc`. This gives Python deterministic destruction: `__del__` methods and context manager `__exit__` calls happen at the exact moment the last reference drops.

```python
import sys

x = []
print(sys.getrefcount(x))  # 2: 1 from x, 1 temporary from the getrefcount argument itself

y = x
print(sys.getrefcount(x))  # 3: 1 from x, 1 from y, 1 temporary from the getrefcount argument

del y
print(sys.getrefcount(x))  # 2: back to 1 from x, 1 temporary from the getrefcount argument
```

**The cycle problem.** Reference counting alone cannot collect cyclic garbage.

```python
import gc

# Create a cycle
class Node:
    def __init__(self, name):
        self.name = name
        self.ref = None

a = Node("A")
b = Node("B")
a.ref = b
b.ref = a   # cycle: A -> B -> A

# Both a and b have refcount >= 1 due to the cycle.
# Neither will be freed by refcounting alone.

del a
del b
# a and b are still alive! Refcount: A has 1 (from b.ref), B has 1 (from a.ref)

# Explicitly trigger the cycle detector
collected = gc.collect()
print(f"Collected {collected} objects")  # Collected 4 objects (2 nodes + 2 dicts)
```

Reference counting handles the common case, but it cannot collect cycles. CPython's answer is a separate cycle detector that runs on top of the reference counting system. The implementation lives in `Modules/gcmodule.c`.

The cycle detector is a tracing collector, but it does not trace the entire heap. It only tracks objects that can participate in cycles: containers like lists, dicts, sets, and user-defined class instances. Strings and integers cannot hold references to other objects, so there is no point tracking them.

Like Java's collectors, the cycle detector uses a generational approach. There are three generations, numbered 0, 1, and 2. The idea is the same as the generational hypothesis we discussed earlier: most objects die young, so check the young ones often and leave the old ones alone. The default thresholds are hardcoded in CPython's [`Modules/gcmodule.c`](https://github.com/python/cpython/blob/v3.9.6/Modules/gcmodule.c#L137):

```c
struct gc_generation generations[NUM_GENERATIONS] = {
    /* PyGC_Head,                                    threshold,    count */
    { {(uintptr_t)_GEN_HEAD(0), (uintptr_t)_GEN_HEAD(0)},   700,        0},
    { {(uintptr_t)_GEN_HEAD(1), (uintptr_t)_GEN_HEAD(1)},   10,         0},
    { {(uintptr_t)_GEN_HEAD(2), (uintptr_t)_GEN_HEAD(2)},   10,         0},
};
```

You can verify what your runtime is actually using:

```bash
python3 -c "import gc; print(gc.get_threshold())"
# (700, 10, 10)
```

Note that some frameworks and distributions override these defaults at startup via `gc.set_threshold()`, so your environment may show different values.

Generation 0 holds newly allocated container objects. When the number of new allocations since the last collection exceeds a threshold (default 700), generation 0 is collected. Objects that survive get promoted to generation 1. Generation 1 is collected after generation 0 has been collected 10 times. Survivors move to generation 2. Generation 2 is collected after generation 1 has been collected 10 times.

The effect is that generation 0 collects roughly every 700 allocations, generation 1 every ~7,000, and generation 2 every ~70,000. Long-lived objects that make it to generation 2 are almost never disturbed. The detector spends most of its time on the youngest objects, which are the most likely to have become garbage recently.

You can see this in action:

```python
import gc

# Current thresholds for each generation
print(gc.get_threshold())  # (700, 10, 10)

# Current allocation counts: (gen0 allocs, gen0 collections since last gen1, gen1 collections since last gen2)
print(gc.get_count())  # e.g., (342, 8, 2)

# Force a full collection across all generations
gc.collect()

# Disable the cycle detector entirely (useful if you know your code has no cycles)
gc.disable()
```

When the detector runs on a generation, it needs to figure out which objects are only kept alive by cycles. A worked example makes the algorithm easier to follow.

Say the detector is looking at three tracked objects: X, Y, and Z. X points to Y and Z. Y points back to X. There is also a local variable holding a reference to X.

```
local_var → X (refcount: 2) → Y (refcount: 1)
             ↑                 |
             +---(Y points to X)
             |
             +→ Z (refcount: 1)
```

Step 1: copy the reference counts. X=2, Y=1, Z=1.

Step 2: subtract internal references. Y points to X, so subtract 1 from X's copy (X goes from 2 to 1). X points to Y, so subtract 1 from Y's copy (Y goes from 1 to 0). X points to Z, so subtract 1 from Z's copy (Z goes from 1 to 0).

Step 3: check what is left. X has an adjusted count of 1. Something outside the tracked set (the local variable) still points to it. X is alive. Y and Z have adjusted counts of 0, but they are reachable from X, so they survive too.

Now imagine the local variable goes away. X's refcount drops to 1 (only Y points to it). Run the same algorithm: copy X=1, Y=1, Z=1. Subtract internals: X goes to 0, Y goes to 0, Z goes to 0. Every adjusted count is zero. Nothing outside the tracked set points to any of them. They are only alive because of each other. All three are garbage.

That is the core idea. The algorithm finds objects whose only reason for existing is other objects in the same set.

There is one edge case that caused real problems for years: finalizers. A finalizer is a method the runtime calls just before an object is destroyed, giving it a chance to clean up external resources like file handles or network connections. In Python, that is the `__del__` method. Say A and B are in a cycle, and both have `__del__` methods. The detector knows they are garbage, but to free them it needs to break the cycle. The question is: which `__del__` runs first? If you run A's finalizer first and it tries to use B, but B is already being torn down, you get a crash. If you run B's first and it uses A, same problem. There is no safe order.

Before Python 3.4, CPython just gave up on these objects. It put them in a list called `gc.garbage` and never freed them. If your code created cycles with `__del__`, you had a silent memory leak. PEP 442 fixed this by calling the finalizers before breaking any references. Both A and B are still fully intact when their `__del__` runs. Only after all finalizers have completed does the detector break the cycle and free the objects.

One more thing worth understanding about CPython's memory model. Every time Python executes something like `x = some_object`, it increments `some_object`'s reference count (`Py_INCREF` in C). Every time a variable goes out of scope, it decrements the count (`Py_DECREF`). These are plain integer operations in C: `refcount += 1`, `refcount -= 1`. No locks, no atomic instructions.

In a multithreaded program, this is a problem. Two threads could increment the same object's refcount at the same time. Without synchronization, one increment gets lost (a classic race condition), and later the object gets freed while someone is still using it.

The GIL prevents this. Only one thread executes Python bytecode at a time, so two threads can never modify the same refcount simultaneously. The GIL makes all reference count operations safe for free, without needing any atomic instructions.

This is also why removing the GIL is so hard. If you take it away, every single `Py_INCREF` and `Py_DECREF` in the entire codebase needs to become an atomic operation. Atomic operations are significantly more expensive than plain integer increments. Python 3.13 began shipping with an experimental free-threaded mode that uses biased reference counting to reduce this cost. The thread that created an object can do cheap non-atomic updates to its refcount. Only other threads accessing the same object pay the atomic cost.

---

## Mapping Back to Wilson: The Full Picture

Every modern collector can be mapped back to the two families Wilson described in 1992. The differences between them are engineering decisions about how to minimize pauses, handle concurrency, and manage memory efficiently.

| | **Java G1GC** | **Java ZGC** | **Go GC** | **Python CPython** |
|---|---|---|---|---|
| **Family** | Tracing | Tracing | Tracing | Ref Counting + Tracing |
| **Variant** | Mark-Copy (young) + Mark-Compact (old) | Concurrent relocating | Mark-Sweep | Mark-Sweep (cycle detector) |
| **Generational** | Yes (young/old) | Yes (Java 21+) | No (experimental) | Yes (3 generations in cyclic GC) |
| **Concurrent** | Partially (mark is concurrent, evacuation is STW) | Mostly (mark and relocate concurrent) | Yes | No (stop-the-world cycle detector) |
| **Compaction** | Yes | Yes (via relocation) | No | No |
| **Typical STW pause** | 1-200ms (tunable) | Sub-millisecond | Sub-millisecond | Rare, short (cycle detector) |
| **Memory overhead** | Moderate | Higher (colored pointers, barriers) | Low-moderate | Low (per-object refcount field) |
| **Primary tuning knob** | `-XX:MaxGCPauseMillis` | Mostly self-tuning | `GOGC`, `GOMEMLIMIT` | `gc.set_threshold()` |

A few observations from this comparison:

**Wilson's tracing family dominates for server runtimes.** Reference counting is used in Swift, Python, and Rust's `Arc`, but for managed runtimes with high allocation rates, tracing collectors are the standard approach. The cycle problem requires a supplementary tracing pass anyway, which adds complexity without eliminating the per-mutation refcount cost.

**Generational collection is everywhere except Go.** Java heavily exploits the generational hypothesis. Python's cycle detector uses three generations. Go initially chose not to use generational collection because the overhead of write barriers for cross-generational pointers was not worth it for Go's typical workloads. That may be changing: experimental generational support has been developed in recent Go versions.

**Compaction vs no compaction is a real design fork.** Java collectors compact, which allows bump-pointer allocation (very fast) and eliminates fragmentation. Go does not compact, which means it never needs to update pointers to moved objects (simpler write barriers, no read barriers needed for correctness). Go compensates with a size-class allocator. This is the classic Wilson tradeoff: copying and compacting collectors trade memory overhead and pointer-update cost for allocation speed and fragmentation elimination.

**ZGC's colored pointers are a modern implementation of Wilson's pointer-tagging idea.** Wilson mentions using bits in pointers for GC metadata. ZGC takes this further by embedding mark state, remap state, and finalization state directly into the 64-bit pointer. The load barrier that checks these bits on every pointer load is the price ZGC pays for sub-millisecond pauses.

**The fundamental problem has not changed.** Tracing from roots, marking what is alive, reclaiming the rest. Everything since 1960 is an engineering refinement of McCarthy's original insight.

---

## Sources

- [McCarthy, J. (1960). Recursive functions of symbolic expressions and their computation by machine, Part I](https://dl.acm.org/doi/10.1145/367177.367199)
- [Wilson, P. R. (1992). Uniprocessor Garbage Collection Techniques. IWMM '92](https://www.cs.rice.edu/~javaplt/311/Readings/wilson92uniprocessor.pdf)
- [A Guide to the Go Garbage Collector](https://tip.golang.org/doc/gc-guide)
- [Getting to Go: The Journey of Go's Garbage Collector](https://go.dev/blog/ismmkeynote)
- [Proposal: Eliminate STW stack re-scanning - Austin Clements (2016)](https://github.com/golang/proposal/blob/master/design/17503-eliminate-rescan.md)
- [Java Garbage Collection: The G1 Garbage Collector](https://www.oracle.com/technetwork/tutorials/tutorials-1876574.html)
- [ZGC: The Z Garbage Collector - JEP 333](https://openjdk.org/jeps/333)
- [Generational ZGC - JEP 439](https://openjdk.org/jeps/439)
- [Design of CPython's Garbage Collector](https://devguide.python.org/internals/garbage-collector/)
- [PEP 442: Safe object finalization](https://peps.python.org/pep-0442/)
