---
layout: post
title: "Java Virtual Threads: The Pinning Problem, the Deadlock, and the Fix in Java 24"
date: 2026-04-25
categories: [java, concurrency, virtual-threads]
tags: [java, virtual-threads, pinning, deadlock, jep-444, jep-491, loom, synchronized, reentrant-lock]
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2Fjava%2Fconcurrency%2Fvirtual-threads%2F2026%2F04%2F25%2Fjava-virtual-threads-pinning-and-the-deadlock-problem.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# Java Virtual Threads: The Pinning Problem, the Deadlock, and the Fix in Java 24

I ran into this in an internal Atlassian engineering writeup. A production service had stalled after adopting virtual threads in Java 21, and the fix was to switch back to platform threads. The writeup also linked to a Netflix engineering blog describing a nearly identical failure: their service stopped serving traffic entirely after enabling virtual threads, with thousands of sockets piling up in `CLOSE_WAIT`.

I had been using virtual threads in a few services and had a rough idea of how they worked, but I did not understand the failure mode. How does adding more threads make a system stop? I went through [JEP 444](https://openjdk.org/jeps/444), [JEP 491](https://openjdk.org/jeps/491), the [Oracle virtual threads documentation](https://docs.oracle.com/en/java/javase/21/core/virtual-threads.html), and the [Netflix blog post](https://netflixtechblog.com/java-21-virtual-threads-dude-wheres-my-lock-3052540e231d) itself. These are my notes from that process.

---

## Virtual Threads

Java has two kinds of threads. A **platform thread** is what Java has always had: a thin wrapper around an OS thread. When you create a platform thread, the JVM asks the OS to allocate a new thread with its own stack (typically around 1 MB by default, configurable via [`-Xss`](https://docs.oracle.com/en/java/javase/21/docs/specs/man/java.html)). The platform thread occupies that OS thread for its entire lifetime. This means the number of platform threads you can have is limited by OS resources, and in practice a few thousand is the upper bound on most systems. If your server uses the thread-per-request model, the number of platform threads becomes the bottleneck long before CPU or network bandwidth are exhausted.

A **virtual thread**, introduced in Java 21 via [JEP 444](https://openjdk.org/jeps/444), is also an instance of `java.lang.Thread`, but it is not tied to a particular OS thread. Its stack lives on the Java heap, not in OS-allocated memory. This makes virtual threads cheap: you can create millions of them without running into OS limits.

The way virtual threads work is by decoupling the Java thread from the OS thread. The JVM maintains a small pool of platform threads called **carrier threads** and schedules virtual threads onto them. The JEP calls this **M:N scheduling**: M virtual threads multiplexed onto N carrier threads, the same idea as goroutines in Go or processes in Erlang.

```
Virtual Threads (millions)        Carrier Threads (few, ~CPU cores)        OS Threads
  ┌──────┐                           ┌──────┐                              ┌──────┐
  │ VT-1 │──── mounted on ──────────>│ CT-1 │───── wraps ────────────────>│ OS-1 │
  ├──────┤                           ├──────┤                              ├──────┤
  │ VT-2 │──── waiting (unmounted)   │ CT-2 │───── wraps ────────────────>│ OS-2 │
  ├──────┤                           └──────┘                              └──────┘
  │ VT-3 │──── waiting (unmounted)
  ├──────┤
  │ ...  │
  ├──────┤
  │VT-10K│──── waiting (unmounted)
  └──────┘
```

The scheduler is a `ForkJoinPool`, which is a thread pool where idle threads can steal tasks from the queues of busy threads. It operates in FIFO mode, meaning tasks are processed in the order they were submitted. By default, its parallelism equals `Runtime.availableProcessors()`, so on a 4-core machine you get 4 carrier threads serving potentially millions of virtual threads.

One thing that tripped me up initially: virtual threads are not faster than platform threads. A virtual thread does not execute your code any faster. The benefit is throughput, not latency. If your application handles 10,000 concurrent requests that each spend 90% of their time waiting for I/O, you need 10,000 threads. With platform threads, that means 10,000 OS threads, which is expensive or impossible. With virtual threads, those 10,000 threads are heap objects scheduled onto a handful of carriers.

---

## Mounting and Unmounting

The scheduling model works because virtual threads can be **mounted** and **unmounted** from carrier threads. When a virtual thread is scheduled, the JVM loads its stack (stored as **stack chunk** objects on the Java heap) onto a carrier, and the carrier starts executing the virtual thread's code.

When the virtual thread hits a blocking operation, like reading from a socket, calling `Thread.sleep()`, or calling `BlockingQueue.take()`, the JVM does something that platform threads cannot do: it saves the virtual thread's stack back to the heap, detaches it from the carrier, and immediately lets the carrier pick up a different virtual thread. The original virtual thread is now parked on the heap, waiting for its I/O to complete, and occupying zero OS resources.

```java
// This single line can cause multiple mount/unmount cycles
response.send(future1.get() + future2.get());
// get() blocks -> VT unmounts -> carrier runs other VTs
// data arrives -> VT remounts (possibly on a different carrier)
// second get() blocks -> unmount again
// and so on
```

The developer never sees any of this. You write the same blocking code you would write with platform threads, `socket.read()`, `future.get()`, `Thread.sleep()`, and the JVM handles the multiplexing underneath. You do not need to restructure your code into callbacks, reactive pipelines, or `CompletableFuture` chains.

Under the hood, this works because of the `Continuation` primitive added to the JVM. When a virtual thread unmounts, the JVM captures its call stack as a **continuation** object on the heap. When the I/O completes, the continuation is resumed on whichever carrier happens to be free (which might be a different carrier from the one it started on). The JDK's I/O libraries (`java.net`, `java.nio`, `java.util.concurrent`) were rewritten to use OS readiness APIs (`epoll` on Linux, `kqueue` on macOS, `IOCP` on Windows), the same primitives that Netty and other reactive frameworks use. The difference is that the developer never has to write code in that style.

This whole scheme depends on the JVM being able to capture the virtual thread's stack at the blocking point. When it cannot do that, the virtual thread stays glued to its carrier. That is the pinning problem.

---

## Creating Virtual Threads

There are two common ways to create virtual threads. The first is `Thread.ofVirtual()`, which gives you a builder:

```java
Thread thread = Thread.ofVirtual()
    .name("my-virtual-thread")
    .start(() -> {
        System.out.println("Running on: " + Thread.currentThread());
        System.out.println("Is virtual: " + Thread.currentThread().isVirtual());
    });
thread.join();
```

The second, and the one you will see more often in server code, is `Executors.newVirtualThreadPerTaskExecutor()`. It creates a new virtual thread for every submitted task:

```java
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    IntStream.range(0, 10_000).forEach(i -> {
        executor.submit(() -> {
            Thread.sleep(Duration.ofSeconds(1));
            return i;
        });
    });
}  // executor.close() is called implicitly, and waits
```

This example is adapted from [JEP 444](https://openjdk.org/jeps/444). It creates 10,000 virtual threads, each sleeping for 1 second. With platform threads, you would need 10,000 OS threads. With virtual threads, the JVM runs all of them on a handful of carriers. The whole thing finishes in roughly 1 second.

One thing to note: virtual threads should never be pooled. They are cheap to create and destroy, so you should create a new one for every task. If you had a thread pool of size 20 to limit concurrent access to a downstream service, do not replace it with a pool of virtual threads. Use a `Semaphore` with 20 permits instead, and let each request run on its own virtual thread.

---

## Pinning

Not all blocking operations allow unmounting. There are two cases where a virtual thread gets **pinned** to its carrier, meaning the carrier thread is blocked along with the virtual thread:

1. When the virtual thread is inside a `synchronized` block or method.
2. When the virtual thread is executing a native method or foreign function (JNI, Foreign Function API).

The first case is the one I wanted to understand, because it is what caused the production failures.

### Why `synchronized` Causes Pinning

To understand why `synchronized` is a problem, you need to know what happens at the JVM level when you write `synchronized(obj)`.

The `synchronized` keyword compiles to two bytecode instructions: `monitorenter` and `monitorexit`. These acquire and release an **object monitor**, which is the JVM's internal locking mechanism. Every Java object has a monitor associated with it. When a thread enters a synchronized block, the JVM records which thread owns that monitor.

Here is the problem: in Java 21, the monitor tracks ownership by OS thread identity. When a virtual thread running on carrier CT-1 enters `synchronized(obj)`, the JVM records "CT-1 owns this monitor." It does not record the virtual thread's identity, because monitors predate virtual threads by decades and were designed around OS threads.

Now suppose the virtual thread hits a blocking I/O call inside that synchronized block. Normally the JVM would unmount the virtual thread, freeing CT-1. But CT-1 still owns the monitor. If the JVM lets CT-1 run a different virtual thread, that new virtual thread would be executing on a carrier that holds a lock it never acquired. Worse, if the new virtual thread tries to enter the same `synchronized(obj)` block, the JVM sees "CT-1 already owns this monitor" and allows re-entry (monitors are reentrant), breaking mutual exclusion entirely.

The JVM has no safe choice except to keep the virtual thread pinned to the carrier until `monitorexit`.

Let me trace through the exact sequence:

1. VT-1 is running on carrier CT-1.
2. VT-1 enters `synchronized(obj)`. The JVM records CT-1 as the monitor owner (because monitors track OS threads, not virtual threads).
3. VT-1 hits a blocking I/O call inside the synchronized block.
4. Normally the JVM would unmount VT-1 from CT-1, freeing CT-1 to run other virtual threads.
5. But if CT-1 runs VT-2 next, CT-1 still holds the monitor. VT-2 is now executing on a carrier that owns a lock VT-2 never acquired. If VT-2 enters the same `synchronized` block, the JVM sees "CT-1 already holds this monitor" and lets it re-enter (monitor re-entrancy), breaking mutual exclusion.
6. The only safe option is to not unmount at all. VT-1 stays pinned to CT-1 until `monitorexit`.

```
VT-1 on carrier CT-1:
  synchronized (sharedObject) {     <-- monitorenter: CT-1 acquires monitor
      data = socket.read();         <-- blocking I/O: VT-1 CANNOT unmount
                                        CT-1 is now PINNED and BLOCKED
      process(data);
  }                                 <-- monitorexit: only now is CT-1 freed

What would happen if the JVM unmounted VT-1 and scheduled VT-2 on CT-1?

VT-2 on carrier CT-1:
  synchronized (sharedObject) {     <-- monitorenter: CT-1 already holds monitor
                                        JVM allows re-entry (monitor is reentrant)
                                        VT-2 is now inside the lock it never acquired
      // mutual exclusion is broken
  }
```

Pinning by itself does not make an application incorrect. A pinned virtual thread still works, it just holds onto its carrier longer than it should. The problem is scalability: every pinned carrier is a carrier that cannot serve other virtual threads. And the scheduler does not compensate. The `ForkJoinPool` has a fixed number of carrier threads and does not spin up extras when carriers get pinned. If you have 4 carriers and 2 are pinned, you are running on 2. If all 4 are pinned, you are running on zero.

### `ReentrantLock` and `LockSupport.park()`

`ReentrantLock` from `java.util.concurrent.locks` uses `LockSupport.park()` internally to block threads waiting for the lock. `LockSupport.park()` is virtual-thread-aware. When a virtual thread parks on a `ReentrantLock`, the JVM can safely unmount the virtual thread from its carrier. The carrier is freed immediately to run other virtual threads.

That is the difference between the two locking mechanisms:
- `synchronized` uses `monitorenter`, which is tied to the OS thread. Pins the carrier.
- `ReentrantLock` uses `LockSupport.park()`, which is virtual-thread-aware. Frees the carrier.

---

## From Pinning to Deadlock

Pinning by itself does not cause a deadlock. A single pinned virtual thread just wastes one carrier temporarily. The deadlock happens when pinning exhausts all carrier threads at the same time:

1. The JVM has N carrier threads (e.g., 2 on a 2-core machine, or configured via `-Djdk.virtualThreadScheduler.parallelism=2`).
2. Multiple virtual threads compete for a shared `synchronized` lock.
3. VT-1 acquires the lock and enters the synchronized block.
4. VT-1 performs a blocking operation inside the block (network I/O, sleep, waiting for a response). VT-1 is now pinned to carrier CT-1.
5. VT-2 is scheduled on carrier CT-2. VT-2 tries to enter the same synchronized block. It blocks waiting for the monitor. VT-2 is now pinned to carrier CT-2.
6. All carrier threads are now pinned. No carrier is available to run any other virtual thread.
7. VT-1 is still waiting for its blocking operation to complete, but the response processing might itself require a virtual thread to run, and no carrier is available.
8. The system is stuck.

```
State at deadlock:

CT-1 (pinned): VT-1 holds lock, blocked on I/O inside synchronized block
CT-2 (pinned): VT-2 waiting for lock (monitorenter), cannot unmount

Carrier pool: 0 available
Queued VTs:   VT-3, VT-4, ... VT-10000 (all waiting for a carrier)

Result: No progress possible. System hangs.
```

In a traditional deadlock, thread A holds lock 1 and waits for lock 2, while thread B holds lock 2 and waits for lock 1. That is not what happens here. No thread is waiting for a lock held by another thread. Instead, all carriers are consumed by pinned virtual threads, and no carrier is available to make forward progress. The scheduler has work to do (virtual threads are queued) but no carrier to do it on.

---

## Reproducing the Deadlock Locally

Here is a complete, runnable Java 21 program that demonstrates carrier exhaustion caused by pinning. Save it as `VirtualThreadPinningDemo.java`.

The demo gives each virtual thread its own independent lock object, so all threads can enter their synchronized blocks concurrently. Each one pins a carrier while sleeping inside the block. With 2 carriers and 4 threads, only 2 can run at a time. The other 2 sit in the scheduler queue, waiting for a carrier to become free. The `ReentrantLock` version does the same work, but virtual threads unmount during sleep, so all 4 finish in ~2 seconds on the same 2 carriers.

```java
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.locks.ReentrantLock;

/**
 * Demonstrates virtual thread pinning leading to carrier exhaustion.
 *
 * Run with:
 *   javac VirtualThreadPinningDemo.java
 *   java -Djdk.virtualThreadScheduler.parallelism=2 VirtualThreadPinningDemo
 *
 * Optional: add -Djdk.tracePinnedThreads=full to see pinning stack traces.
 */
public class VirtualThreadPinningDemo {

    private static final int NUM_THREADS = 4;
    // Each thread gets its OWN lock so they can all enter simultaneously
    private static final Object[] LOCKS = new Object[NUM_THREADS];
    static {
        for (int i = 0; i < NUM_THREADS; i++) LOCKS[i] = new Object();
    }

    public static void main(String[] args) throws Exception {
        int carriers = Integer.getInteger("jdk.virtualThreadScheduler.parallelism",
                Runtime.getRuntime().availableProcessors());
        System.out.println("Carrier threads: " + carriers);
        System.out.println("Virtual threads: " + NUM_THREADS);
        System.out.println();

        System.out.println("=== Part 1: synchronized (carriers get exhausted) ===\n");
        demonstrateSynchronizedPinning(carriers);

        System.out.println("\n=== Part 2: ReentrantLock (carriers stay free) ===\n");
        demonstrateReentrantLockFix(carriers);
    }

    static void demonstrateSynchronizedPinning(int carriers) throws Exception {
        AtomicInteger completed = new AtomicInteger(0);
        long start = System.currentTimeMillis();

        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            for (int i = 0; i < NUM_THREADS; i++) {
                final int id = i;
                executor.submit(() -> {
                    long t = System.currentTimeMillis() - start;
                    System.out.printf("[%4dms] VT-%d entering synchronized block%n", t, id);
                    synchronized (LOCKS[id]) {
                        t = System.currentTimeMillis() - start;
                        System.out.printf("[%4dms] VT-%d acquired lock, sleeping 2s (carrier PINNED)%n", t, id);
                        try {
                            Thread.sleep(2000);
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                        }
                        t = System.currentTimeMillis() - start;
                        System.out.printf("[%4dms] VT-%d done%n", t, id);
                    }
                    completed.incrementAndGet();
                    return null;
                });
            }
            executor.close();
        }

        long elapsed = System.currentTimeMillis() - start;
        System.out.println("\nSynchronized result:");
        System.out.println("  Completed: " + completed.get() + "/" + NUM_THREADS);
        System.out.println("  Elapsed:   " + elapsed + "ms");
        System.out.println("  Why: Each sleeping VT pins its carrier. Only " + carriers
            + " can run at a time.");
    }

    static void demonstrateReentrantLockFix(int carriers) throws Exception {
        AtomicInteger completed = new AtomicInteger(0);
        ReentrantLock[] locks = new ReentrantLock[NUM_THREADS];
        for (int i = 0; i < NUM_THREADS; i++) locks[i] = new ReentrantLock();

        long start = System.currentTimeMillis();

        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            for (int i = 0; i < NUM_THREADS; i++) {
                final int id = i;
                executor.submit(() -> {
                    long t = System.currentTimeMillis() - start;
                    System.out.printf("[%4dms] VT-%d acquiring ReentrantLock%n", t, id);
                    locks[id].lock();
                    try {
                        t = System.currentTimeMillis() - start;
                        System.out.printf("[%4dms] VT-%d acquired lock, sleeping 2s (carrier FREE)%n", t, id);
                        Thread.sleep(2000);
                        t = System.currentTimeMillis() - start;
                        System.out.printf("[%4dms] VT-%d done%n", t, id);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                    } finally {
                        locks[id].unlock();
                    }
                    completed.incrementAndGet();
                    return null;
                });
            }
            executor.close();
        }

        long elapsed = System.currentTimeMillis() - start;
        System.out.println("\nReentrantLock result:");
        System.out.println("  Completed: " + completed.get() + "/" + NUM_THREADS);
        System.out.println("  Elapsed:   " + elapsed + "ms");
        System.out.println("  Why: VTs unmount during Thread.sleep(), " + carriers
            + " carriers serve all " + NUM_THREADS + " VTs concurrently.");
    }
}
```

### Running the Demo

```bash
# Compile
javac VirtualThreadPinningDemo.java

# Run with 2 carrier threads to see the effect clearly
java -Djdk.virtualThreadScheduler.parallelism=2 -Djdk.tracePinnedThreads=full VirtualThreadPinningDemo
```

### Expected Output

Run with `-Djdk.tracePinnedThreads=full` to see both the timing and the pinning stack traces:

```
=== Part 1: synchronized (carriers get exhausted) ===

[   9ms] VT-1 entering synchronized block
[  21ms] VT-1 acquired lock, sleeping 2s (carrier PINNED)
[   9ms] VT-0 entering synchronized block
[  22ms] VT-0 acquired lock, sleeping 2s (carrier PINNED)
VirtualThread[#20]/runnable@ForkJoinPool-1-worker-1 reason:MONITOR
    java.base/java.lang.VirtualThread$VThreadContinuation.onPinned(VirtualThread.java:199)
    java.base/jdk.internal.vm.Continuation.onPinned0(Continuation.java:393)
    java.base/java.lang.VirtualThread.parkNanos(VirtualThread.java:635)
    java.base/java.lang.VirtualThread.sleepNanos(VirtualThread.java:807)
    java.base/java.lang.Thread.sleep(Thread.java:507)
    VirtualThreadPinningDemo.lambda$demonstrateSynchronizedPinning$0(VirtualThreadPinningDemo.java:51) <== monitors:1
    java.base/java.util.concurrent.FutureTask.run(FutureTask.java:317)
    java.base/java.lang.VirtualThread.run(VirtualThread.java:329)
[2024ms] VT-0 done
[   9ms] VT-2 entering synchronized block
[2029ms] VT-2 acquired lock, sleeping 2s (carrier PINNED)
[2024ms] VT-1 done
[4030ms] VT-2 done
[   9ms] VT-3 entering synchronized block
[4032ms] VT-3 acquired lock, sleeping 2s (carrier PINNED)
[6034ms] VT-3 done

Synchronized result:
  Completed: 4/4
  Elapsed:   6035ms

=== Part 2: ReentrantLock (carriers stay free) ===

[   2ms] VT-0 acquired lock, sleeping 2s (carrier FREE)
[   3ms] VT-1 acquired lock, sleeping 2s (carrier FREE)
[   3ms] VT-2 acquired lock, sleeping 2s (carrier FREE)
[   4ms] VT-3 acquired lock, sleeping 2s (carrier FREE)
[2004ms] VT-0 done
[2004ms] VT-1 done
[2004ms] VT-2 done
[2004ms] VT-3 done

ReentrantLock result:
  Completed: 4/4
  Elapsed:   2006ms
```

**Reading the pinning trace.** The JVM prints a stack trace every time a virtual thread blocks while pinned. The key markers are:

- `reason:MONITOR` tells you the virtual thread is pinned because it is inside a `synchronized` block.
- `<== monitors:1` on the `VirtualThreadPinningDemo.lambda` frame points to the exact line of code holding the monitor.
- The trace shows `VirtualThread.parkNanos` calling `Continuation.onPinned0`, which is the JVM's "I wanted to unmount but cannot" path.

**Why 6 seconds instead of 4.** VT-0 and VT-1 start immediately and pin both carriers for 2 seconds. VT-2 and VT-3 are submitted at 9ms but cannot run because no carrier is available. When VT-0 finishes at ~2024ms, a carrier is freed and VT-2 gets scheduled. But VT-3 has to wait again. The actual batching ends up as three batches instead of the theoretical two:

- Batch 1 (0 to 2s): VT-0, VT-1
- Batch 2 (2 to 4s): VT-2
- Batch 3 (4 to 6s): VT-3

The extra 2 seconds come from pinned carriers not releasing cleanly at the exact same instant. Carrier release, virtual thread scheduling, and remounting all have overhead, and this overhead compounds when the scheduler is already starved.

**ReentrantLock comparison.** All 4 threads acquire their locks and enter `Thread.sleep()` within the first 4ms. The virtual threads unmount during sleep, freeing the carriers immediately. Both carriers serve all 4 virtual threads concurrently, and everything finishes in ~2 seconds. No pinning traces are printed.

The difference is 3x in this simple example. In production, with hundreds of virtual threads, limited carriers, and `synchronized` blocks in library code (JDBC drivers, caches, HTTP clients), the carriers get fully exhausted and the application hangs.

### Diagnosing Pinning with JVM Flags

**`-Djdk.tracePinnedThreads=full`**: Prints a full stack trace every time a virtual thread blocks while pinned. The output highlights native frames and frames holding monitors:

```bash
java -Djdk.tracePinnedThreads=full -jar myapp.jar
```

**`-Djdk.tracePinnedThreads=short`**: Prints abbreviated output showing just the problematic frames.

**`-Djdk.virtualThreadScheduler.parallelism=N`**: Controls the number of carrier threads. Setting this to a low value (1 or 2) makes pinning issues easier to reproduce during testing.

**JDK Flight Recorder (JFR)** is a built-in JVM profiling and diagnostics tool that records events about the JVM's behavior with very low overhead. The `jdk.VirtualThreadPinned` event is emitted when a thread blocks while pinned. It is enabled by default with a threshold of 20 ms. You can capture it with:

```bash
jcmd <pid> JFR.start name=pinning duration=60s filename=pinning.jfr
```

**Thread Dumps**: Use `jcmd` to generate virtual-thread-aware thread dumps:

```bash
jcmd <pid> Thread.dump_to_file -format=json threaddump.json
jcmd <pid> Thread.dump_to_file -format=text threaddump.txt
```

Here is what the thread dump looks like when captured during pinning with our demo (running with `-Djdk.virtualThreadScheduler.parallelism=2` and 4 virtual threads). The relevant threads, stripped of JVM internals:

```
#21 "ForkJoinPool-1-worker-1"                         <-- carrier thread 1
      java.base/jdk.internal.vm.Continuation.run(Continuation.java:251)
      java.base/java.lang.VirtualThread.runContinuation(VirtualThread.java:245)
      java.base/java.util.concurrent.ForkJoinPool.scan(ForkJoinPool.java:1843)
      java.base/java.util.concurrent.ForkJoinPool.runWorker(ForkJoinPool.java:1808)
      java.base/java.util.concurrent.ForkJoinWorkerThread.run(ForkJoinWorkerThread.java:188)

#25 "ForkJoinPool-1-worker-2"                         <-- carrier thread 2
      java.base/jdk.internal.vm.Continuation.run(Continuation.java:251)
      java.base/java.lang.VirtualThread.runContinuation(VirtualThread.java:245)
      java.base/java.util.concurrent.ForkJoinPool.scan(ForkJoinPool.java:1843)
      java.base/java.util.concurrent.ForkJoinPool.runWorker(ForkJoinPool.java:1808)
      java.base/java.util.concurrent.ForkJoinWorkerThread.run(ForkJoinWorkerThread.java:188)

#23 "" virtual                                        <-- pinned VT: sleeping inside synchronized
      java.base/jdk.internal.misc.Unsafe.park(Native Method)
      java.base/java.lang.VirtualThread.parkOnCarrierThread(VirtualThread.java:677)
      java.base/java.lang.VirtualThread.parkNanos(VirtualThread.java:648)
      java.base/java.lang.VirtualThread.sleepNanos(VirtualThread.java:807)
      java.base/java.lang.Thread.sleep(Thread.java:507)
      VirtualThreadPinningDemo.lambda$main$0(VirtualThreadPinningDemo.java:35)

#22 "" virtual                                        <-- pinned VT: waiting for PrintStream lock
      java.base/jdk.internal.misc.Unsafe.park(Native Method)
      java.base/java.lang.VirtualThread.parkOnCarrierThread(VirtualThread.java:675)
      java.base/java.util.concurrent.locks.LockSupport.park(LockSupport.java:219)
      java.base/java.util.concurrent.locks.ReentrantLock.lock(ReentrantLock.java:322)
      java.base/jdk.internal.misc.InternalLock.lock(InternalLock.java:74)
      java.base/java.io.PrintStream.printf(PrintStream.java:1245)
      VirtualThreadPinningDemo.lambda$main$0(VirtualThreadPinningDemo.java:40)

#24 "" virtual                                        <-- unmounted VT: waiting for carrier
      java.base/java.lang.VirtualThread.park(VirtualThread.java:596)
      java.base/java.util.concurrent.locks.LockSupport.park(LockSupport.java:219)
      java.base/java.util.concurrent.locks.ReentrantLock.lock(ReentrantLock.java:322)
      java.base/jdk.internal.misc.InternalLock.lock(InternalLock.java:74)
      java.base/java.io.PrintStream.printf(PrintStream.java:1245)
      VirtualThreadPinningDemo.lambda$main$0(VirtualThreadPinningDemo.java:30)
```

**How to read this.** The key diagnostic signal is the code path through `VirtualThread`:

- `VirtualThread.parkOnCarrierThread` = the virtual thread is **pinned**. It wanted to unmount but could not because it holds a monitor. The carrier is stuck.
- `VirtualThread.park` (without "OnCarrierThread") = the virtual thread **unmounted successfully**. It is parked on the heap, and its carrier is free to run other virtual threads.

Walking through each thread:

**#21 and #25 (carrier threads).** Both show `Continuation.run` → `VirtualThread.runContinuation` → `ForkJoinPool.scan` → `ForkJoinWorkerThread.run`. These are the two `ForkJoinPool` worker threads (the carriers). `Continuation.run` means each carrier is currently executing a virtual thread's continuation. Both carriers are occupied.

**#23 (pinned virtual thread, sleeping).** The stack reads bottom-up: `VirtualThread.run` → our lambda → `Thread.sleep` → `VirtualThread.sleepNanos` → `VirtualThread.parkNanos` → `VirtualThread.parkOnCarrierThread`. This virtual thread entered a `synchronized` block, called `Thread.sleep()`, and the JVM tried to unmount it. But because it holds a monitor, the JVM took the `parkOnCarrierThread` path instead of unmounting. The carrier is now blocked waiting for the sleep to finish.

**#22 (pinned virtual thread, blocked on PrintStream).** The stack reads: `VirtualThread.run` → our lambda → `PrintStream.printf` → `InternalLock.lock` → `ReentrantLock.lock` → `LockSupport.park` → `VirtualThread.parkOnCarrierThread`. This virtual thread is also inside a `synchronized` block (it holds a monitor), and it called `System.out.printf()`. Internally, `PrintStream.format()` acquires a `ReentrantLock`. Normally, parking on a `ReentrantLock` would unmount the virtual thread. But because this VT already holds a monitor from the outer `synchronized` block, the JVM cannot unmount it. So even the `ReentrantLock` park goes through `parkOnCarrierThread`, and the carrier is stuck.

**#24 (unmounted virtual thread).** The stack reads: `VirtualThread.run` → our lambda → `PrintStream.printf` → `InternalLock.lock` → `ReentrantLock.lock` → `LockSupport.park` → `VirtualThread.park`. This VT is doing the same thing as #22 (waiting for the `PrintStream` internal lock), but its stack shows `VirtualThread.park` instead of `parkOnCarrierThread`. This VT has **not** entered its `synchronized` block yet. It does not hold a monitor, so the JVM was able to unmount it normally. It is parked on the heap, not occupying a carrier. But even when the `PrintStream` lock becomes available, #24 will need a free carrier to resume, and both carriers are pinned by #22 and #23.


---

## Netflix: Pinning in Production

Netflix documented this exact failure mode in a blog post titled ["Java 21 Virtual Threads - Dude, Where's My Lock?"](https://netflixtechblog.com/java-21-virtual-threads-dude-wheres-my-lock-3052540e231d), published in July 2024. Reading it is what made the pinning problem click for me, because it shows how it plays out in real production code rather than a contrived demo.

### What Happened

Netflix was running Java 21 with SpringBoot 3 and embedded Tomcat. After enabling virtual threads for request handling, they started seeing intermittent timeouts and hung instances. Applications would stop serving traffic entirely while the JVM remained alive. The telltale symptom was thousands of sockets stuck in `CLOSE_WAIT` state. `CLOSE_WAIT` is a TCP socket state that means the remote side has closed the connection, but the local application has not yet closed its end. Sockets piling up in this state usually indicate the application is stuck and not processing connections.

### Tracing It to Brave

The problem traced back to the Brave/Zipkin distributed tracing library. When a request completed, the code called `brave.RealSpan.finish()`, which used a `synchronized` block internally. Inside that synchronized block, the code attempted to acquire a `ReentrantLock` for reporting. Here is the sequence:

1. Virtual thread handles an HTTP request via Tomcat
2. Request completes, calls `RealSpan.finish()`
3. `RealSpan.finish()` enters a `synchronized(state)` block
4. Inside the synchronized block, `pendingSpans.finish()` is called, which flows downstream into `CountBoundedQueue.offer()`. This method acquires a `ReentrantLock`
5. The `ReentrantLock` is held by another thread, so the virtual thread blocks
6. Because the block happens inside a `synchronized` block, the virtual thread is pinned. It cannot unmount
7. The carrier thread is stuck

With 4 vCPUs, Netflix had 4 carrier threads. After 4 virtual threads got pinned inside `RealSpan.finish()`, the carrier pool was exhausted. No new requests could be served.

### Why the System Hung

Tomcat kept accepting connections and creating virtual threads for each request, but those threads could not be scheduled because all carriers were pinned. They sat in the scheduler queue while still holding the socket, which explains the climbing `CLOSE_WAIT` count.

The heap dump told the full story:

- The `ReentrantLock`'s `exclusiveOwnerThread` was `null`. The lock had already been released
- 6 threads were waiting for the same lock: 5 virtual threads + 1 platform thread
- 4 of the 5 virtual threads were pinned to carrier threads
- The lock was in a transient state: released, but the next waiter could not proceed because no carrier was available to run it

The lock holder releases the lock, the next thread gets notified, but that thread cannot run because all carriers are pinned. The system is permanently stuck.

### What Made This Hard to Catch

The `synchronized` block was not in Netflix's own code. It was inside a third-party library (Brave). The developers had no idea that a tracing library was using `synchronized` in a way that could exhaust carrier threads. You cannot always control which libraries use `synchronized` internally, and you cannot always read the source of every transitive dependency on your classpath.

---

## Broader Ecosystem Impact

Netflix was not the only one hit by pinning. The problem showed up across the Java ecosystem as teams adopted virtual threads.

### Spring Framework

Spring Boot 3.2 added a simple property to enable virtual threads for Tomcat request handling:

```yaml
spring:
  threads:
    virtual:
      enabled: true
```

Source: [Spring Boot 3.2 Release Notes](https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-3.2-Release-Notes#support-for-virtual-threads).

### Apache HTTP Client

Apache HTTP Client 5 (before version 5.4) had `synchronized` blocks in `PoolingHttpClientConnectionManager.lease()` that could pin virtual threads during network operations. Version 5.4 "ensures compatibility with Java Virtual Threads by replacing 'synchronized' keywords in critical sections with Java lock primitives." Source: [HttpClient 5.4 Release Notes](https://downloads.apache.org/httpcomponents/httpclient/RELEASE_NOTES-5.4.x.txt).

### Caffeine Cache

Caffeine is layered on top of `ConcurrentHashMap`, which itself uses `synchronized` monitors internally. This means synchronous cache operations like `cache.get(key, loader)` will pin virtual threads regardless of what Caffeine does at its own layer. The maintainer noted this was a JDK-level problem: until `ConcurrentHashMap` or the JVM's monitor implementation changed, virtual thread pinning during cache computations was unavoidable. The recommended workaround was to use `AsyncCache` instead. Source: [caffeine#1018](https://github.com/ben-manes/caffeine/issues/1018).

### JDBC Drivers

JDBC is fundamentally blocking. Every JDBC call (executing a query, reading a result set) blocks the calling thread. Some JDBC drivers also used `synchronized` internally in ways that interacted badly with virtual threads in Java 21 through 23.

A community contribution replaced these with `ReentrantLock`, shipped in Connector/J 9.0.0: "Synchronized blocks in the Connector/J code were replaced with ReentrantLocks. This allows carrier threads to unmount virtual threads when they are waiting on IO operations, making Connector/J virtual-thread friendly." Source: [MySQL Connector/J bug 110512](https://bugs.mysql.com/bug.php?id=110512).

The PostgreSQL JDBC driver tracked the same issue. Source: [pgjdbc#1951](https://github.com/pgjdbc/pgjdbc/issues/1951).

---

## Java 24: JEP 491

JEP 491, titled "Synchronize Virtual Threads without Pinning," was delivered in Java 24. It rewrites the JVM's monitor implementation to be virtual-thread-aware.

### What Changed

In Java 21 through 23, as I described above, object monitors tracked ownership by OS thread identity. `monitorenter` associated the lock with the carrier thread, and that made unmounting impossible.

Java 24 changes this at the JVM level. The monitor is now associated with the virtual thread itself, not the carrier. This one change makes the rest possible:

- When a virtual thread blocks on I/O or `Thread.sleep()` inside a synchronized block, the JVM can now unmount it and free the carrier, because the monitor stays with the virtual thread, not the carrier.
- When the blocking operation completes, the virtual thread can be remounted on any available carrier, and it still owns the monitor. No lock semantics are violated.
- `Object.wait()` inside a synchronized block also works correctly. `Object.wait()` has always released the monitor before sleeping (that is core Java semantics since 1.0). The change in JEP 491 is about operations that do not release the monitor, like blocking I/O and `Thread.sleep()`. In Java 24, those operations can now unmount too.

```
Before JEP 491 (Java 21-23):

  synchronized (obj) {         <-- carrier CT-1 acquires monitor
      data = socket.read();    <-- blocking I/O: VT pinned, CT-1 blocked
  }

After JEP 491 (Java 24+):

  synchronized (obj) {         <-- VT-1 acquires monitor (not tied to carrier)
      data = socket.read();    <-- blocking I/O: VT-1 unmounts, CT-1 freed
                               <-- VT-1 parked in JVM scheduler queue
  }                            <-- when I/O completes: VT-1 remounts (maybe on CT-2)
```

### What Still Pins

JEP 491 eliminates pinning for `synchronized` blocks, but pinning can still occur in one specific case:

- **Native code and foreign functions.** When a virtual thread calls a native method via JNI or the Foreign Function and Memory API, it must execute on the OS thread. The JVM cannot unmount the virtual thread mid-execution of native code because the native code may manipulate thread-local storage or call blocking OS APIs. This is a fundamental limitation of the Java-native boundary.

For most server applications, native code is not on the request path, so this remaining case does not affect scalability.

### No Code Changes Required

JEP 491 requires no code changes. Existing applications with `synchronized` blocks automatically benefit from the fix when they upgrade to Java 24 (or Java 25 LTS, which inherits the fix). The same code that caused deadlocks on Java 21 runs correctly on Java 24.

Running the `VirtualThreadPinningDemo` from earlier on Java 24:

```bash
# Same code, same flags, different result
java -Djdk.virtualThreadScheduler.parallelism=2 VirtualThreadPinningDemo
```

The synchronized version now runs without pinning the carriers. Virtual threads unmount during `Thread.sleep()` even though they are inside a synchronized block.

---

## Practical Guidelines

Based on everything above, here is what I would tell someone adopting virtual threads today:

**Do not pool virtual threads.** Create a new virtual thread for every task. Use `Executors.newVirtualThreadPerTaskExecutor()` or `Thread.ofVirtual().start()`. If you need to limit concurrency, use a `Semaphore`.

**On Java 21 through 23, replace `synchronized` with `ReentrantLock`** in code that runs on virtual threads and performs blocking operations inside the critical section. Short, non-blocking synchronized blocks are fine. As the JEP notes, there is no need to replace synchronized blocks that guard short-lived or infrequent operations.

**On Java 24+, `synchronized` is safe again.** JEP 491 eliminates the pinning problem for synchronized blocks. You do not need to refactor existing code.

**Watch out for third-party libraries.** The Netflix incident was caused by a `synchronized` block inside Brave, not in their own code. Use `-Djdk.tracePinnedThreads=full` during testing to identify pinning in dependencies.

**Virtual threads help when the workload is I/O-bound.** If your application spends most of its time waiting for network responses, database queries, or file I/O, virtual threads will improve throughput by keeping carriers busy while other virtual threads wait. If the workload is CPU-bound (image processing, cryptography, heavy computation), virtual threads will not help. Having more threads than cores does not give you more CPU cycles.

**Be careful with `ThreadLocal`.** With platform threads, you might store a database connection or a `SimpleDateFormat` in a `ThreadLocal` and reuse it across requests that happen to land on the same thread. With virtual threads, each thread is short-lived and gets its own `ThreadLocal`, so storing expensive resources there means creating one per request. Use connection pools and thread-safe formatters instead.

**Use JFR for production monitoring.** The `jdk.VirtualThreadPinned` event (enabled by default with a 20 ms threshold) will alert you to pinning in production without adding overhead.

| Java Version | `synchronized` | `ReentrantLock` | Native/JNI |
|---|---|---|---|
| Java 21-23 | Pins carrier | Safe (no pinning) | Pins carrier |
| Java 24+ | Safe (JEP 491) | Safe (no pinning) | Pins carrier |

---

## Sources

- [OpenJDK, 2023. JEP 444: Virtual Threads](https://openjdk.org/jeps/444)
- [OpenJDK, 2024. JEP 491: Synchronize Virtual Threads without Pinning](https://openjdk.org/jeps/491)
- [Oracle, 2023. Java SE 21 Core Libraries: Virtual Threads](https://docs.oracle.com/en/java/javase/21/core/virtual-threads.html)
- [Netflix Technology Blog, 2024. Java 21 Virtual Threads: Dude, Where's My Lock?](https://netflixtechblog.com/java-21-virtual-threads-dude-wheres-my-lock-3052540e231d)
- [Spring Boot, 2023. Spring Boot 3.2 Release Notes](https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-3.2-Release-Notes)
- [Apache, 2024. HttpClient 5.4 Release Notes](https://downloads.apache.org/httpcomponents/httpclient/RELEASE_NOTES-5.4.x.txt)
- [Caffeine, 2023. GitHub Issue #1018: Make Virtual-Thread-Friendly](https://github.com/ben-manes/caffeine/issues/1018)
- [MySQL, 2023. Bug #110512: Replace synchronized with ReentrantLock](https://bugs.mysql.com/bug.php?id=110512)
- [PostgreSQL JDBC, 2022. Issue #1951: Loom-Friendly Driver](https://github.com/pgjdbc/pgjdbc/issues/1951)
