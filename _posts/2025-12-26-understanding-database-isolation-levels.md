---
layout: post
title: "Understanding Database Transactions and Isolation Levels"
date: 2025-12-26
categories: [databases, transactions, isolation-levels]
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2Fdatabases%2Ftransactions%2Fisolation-levels%2F2025%2F12%2F27%2Funderstanding-database-isolation-levels.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# Understanding Database Transactions and Isolation Levels

I always wanted to understand database transaction isolation levels better, and to figure out which one fits which use case.
So I am writing this post as my own notes from reading and learning about these concepts. While working with databases, understanding how concurrent transactions interact is crucial for building correct and performant applications.

## Setting the Context: Transactions and ACID

Before talking about isolation levels, let me briefly define a few terms.

**What is a transaction?** A transaction is a sequence of one or more database operations (reads, writes, updates, deletes) that are executed as a single logical unit of work. Either all operations in the transaction succeed, or none of them do.

**What is ACID?** ACID is a set of properties that guarantee reliable processing of database transactions. ACID stands for Atomicity, Consistency, Isolation, and Durability.

**How is ACID related to transactions?** ACID properties define the guarantees a database provides when executing transactions. They ensure that transactions are processed reliably even in the presence of failures, concurrent access, and other edge cases.

**Where do isolation levels fit in?** Isolation is one of the four ACID properties. Isolation levels define how transactions are isolated from each other when multiple transactions run concurrently. This is what we'll focus on in this post.

Let me quickly define each ACID property with examples.

**Atomicity** means all or nothing. When you transfer money from Account A to Account B, either both the debit and credit happen, or neither does. There's no in-between state where money vanishes.

**Consistency** ensures the database moves from one valid state to another. If your schema says account balances can't be negative, any transaction attempting to violate this is rejected.

**Isolation** determines how concurrent transactions affect each other. Different isolation levels provide different guarantees about what one transaction can see from another concurrent transaction.

**Durability** guarantees that once a transaction commits, it's permanent. Even if the system crashes immediately after, your committed data survives.

## The Core Trade-off

Here's the fundamental challenge: **higher isolation levels prevent more anomalies, but they reduce concurrency and hurt performance.**

Strong isolation means fewer data anomalies but more locks and waiting. Weak isolation means better throughput but more potential issues. You need to choose based on your workload.

## How Isolation is Implemented: Locking Mechanisms

Before we dive into the isolation levels, let me briefly explain the different locking mechanisms databases use to implement isolation.

**Row Locks** lock specific rows being read or modified. This allows concurrent access to different rows in the same table. Most commonly used for implementing isolation.

**Table Locks** lock entire tables. They provide low concurrency but are simple. Rarely used for isolation itself, more commonly for DDL operations like schema changes.

**Range Locks / Gap Locks** lock ranges of values or gaps between existing rows. Used to prevent phantom reads by ensuring no new rows can be inserted in a range being read.

**Shared Locks (S-locks)** allow multiple transactions to read the same data concurrently, but prevent writes while the lock is held.

**Exclusive Locks (X-locks)** prevent both reads and writes by other transactions. Only one transaction can hold an exclusive lock on a resource at a time.

**MVCC (Multi-Version Concurrency Control)** is an alternative to locking. Instead of locking, MVCC keeps multiple versions of data. Each transaction sees a consistent snapshot. Writers create new versions instead of overwriting. Readers never block writers and vice versa. PostgreSQL, MySQL InnoDB, and Oracle use MVCC.

Now let's look at the anomalies and isolation levels.

## What Can Go Wrong: Read Anomalies

Let me define the types of consistency issues that can occur when transactions run concurrently.

**Dirty Reads** happen when you read uncommitted data from another transaction that might later rollback. Transaction A updates a product price from $100 to $80 but hasn't committed. Transaction B reads $80 and displays it. Transaction A rolls back. Your customer saw a price that never actually existed.

**Non-Repeatable Reads** occur when you read the same row twice in a transaction and get different values. You check your balance and see $1000. Another transaction withdraws $200 and commits. You check again and see $800. The data changed under you.

**Phantom Reads** happen when a query returns different rows on re-execution because another transaction inserted or deleted matching rows. You query "all booked seats in row 5" and get 3 seats. Someone books a 4th seat in row 5. You query again and now see 4 seats. A phantom row appeared.

**Lost Updates** occur when two transactions read the same data and both update it, causing one update to overwrite the other. Two users simultaneously buy from inventory showing 10 items. Both read 10, both write 9 after their purchase. Result: inventory is 9 instead of 8. One sale was lost.

Note: Lost updates can occur at any isolation level if not handled properly. Even at higher isolation levels, if you use a read-then-write pattern without proper locking (like `SELECT FOR UPDATE`), lost updates can happen. Some databases provide automatic protection against lost updates at higher isolation levels, but it's not guaranteed by the SQL standard.

## The Four Isolation Levels

The SQL standard defines four isolation levels, from weakest to strongest.

### Read Uncommitted

Transactions can read uncommitted changes from others. This permits dirty reads, non-repeatable reads, phantom reads, and lost updates.

**Use cases:** Analytics where approximate values are fine, monitoring dashboards, scenarios where performance matters more than perfect accuracy.

**Example:** Consider a real-time analytics dashboard showing "total sales today" across thousands of concurrent transactions. At Read Uncommitted, you might read sales data from transactions that are still in-flight and could potentially rollback. But for a dashboard that refreshes every few seconds, this is acceptable. You're willing to accept seeing $50,125 when the actual committed value is $50,100, because:
- The performance gain from zero locking overhead is huge when reading across many rows
- The inaccuracy is temporary and corrects itself on the next refresh
- The business decision (knowing sales are "around 50k") doesn't require exact precision

The consistency guarantees here are minimal, but that's exactly what you need when speed matters more than accuracy.

**Implementation:** Minimal to no locking. Transactions can read rows even while other transactions hold exclusive locks on them for writes. No shared locks are acquired for reads.

### Read Committed

You only read committed data. No dirty reads allowed. This is the default in PostgreSQL, Oracle, and SQL Server.

**Anomalies permitted:** Non-repeatable reads, phantom reads

**Use cases:** Most general-purpose applications, e-commerce browsing, social media feeds, any scenario where seeing committed data is sufficient.

**Example:** Consider browsing products on an e-commerce site. You're looking at a laptop priced at $999.
While you read reviews and specifications (all within a single page load transaction), the price gets updated to $1099
by another transaction that commits. When you click "Add to Cart," you see the new price $1099.

This is perfectly fine because:
- You never saw uncommitted data (no dirty read) - the price you initially saw was committed
- The price change between your reads (non-repeatable read) is acceptable - prices can change while you shop
- When you actually commit to buying (add to cart), you see the current committed price
- Each individual read sees a consistent, committed state of the database

Read Committed prevents the serious problem of seeing data that might never exist (dirty reads), while allowing good concurrency.
For most web applications, this is the sweet spot, you always see real committed data, but you don't lock everything down so much that performance suffers.

**Implementation:** 
- **Locking approach:** Exclusive locks (X-locks) on writes are held until transaction commits. Shared locks (S-locks) on reads are acquired but released immediately after reading each row, not held until commit.
- **MVCC approach:** Databases like PostgreSQL use MVCC where readers see a snapshot of committed data without acquiring locks. Writers still acquire exclusive locks but readers never block.

### Repeatable Read

If you read a row twice, you get the same value. The rows you've read won't change during your transaction. This is the default in MySQL InnoDB.

**Anomalies permitted:** Phantom reads (in theory, though PostgreSQL prevents this too)

**Use cases:** Financial reports needing consistent data, generating invoices where totals must match line items, data exports requiring a consistent snapshot.

**Example:** You're generating a monthly financial report that shows:
1. Total account balance: $100,000
2. Sum of all transactions: $100,000
3. Individual line items that should add up to the sum

At Read Committed, this could fail:
```
Read account balance: $100,000
<another transaction commits adding $5,000>
Read sum of transactions: $105,000
```

Your report shows a mismatch - balance says $100k but transactions sum to $105k. This breaks the fundamental integrity of the report.

At Repeatable Read, this is solved:
- When your transaction starts, it sees a snapshot of the database
- Reading account balance: $100,000
- Another transaction commits adding $5,000, but YOUR transaction doesn't see it
- Reading sum of transactions: $100,000 (same snapshot)
- All numbers in your report are consistent with each other

The consistency guarantee here is crucial: within a single transaction, you see a stable view of the data. This is exactly what you need for reports, data exports, or any operation where internal consistency matters more than seeing the absolute latest data. You're essentially saying "give me a consistent point-in-time snapshot" rather than "give me the latest data at each read."

**Implementation:**
- **Locking approach:** Shared locks (S-locks) on reads are held until transaction commits, not just released immediately. Exclusive locks (X-locks) on writes are also held until commit.
- **MVCC approach:** Each transaction sees a consistent snapshot from when it started. All reads within the transaction see data as of that snapshot point, even if other transactions commit changes.

### Serializable

Transactions execute as if they ran one after another, never concurrently. Complete isolation. No anomalies permitted.

**Use cases:** Banking transactions, inventory management with exact counts, ticket/seat reservations, any scenario where data correctness is absolutely critical.

**Example:** Consider a banking transfer from Account A to Account B:

```sql
BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;

-- Check if Account A has sufficient balance
SELECT balance FROM accounts WHERE account_id = 'A';  -- Returns $1000

-- Check if this would violate any constraints
IF balance >= transfer_amount THEN
  UPDATE accounts SET balance = balance - 500 WHERE account_id = 'A';
  UPDATE accounts SET balance = balance + 500 WHERE account_id = 'B';
END IF;

COMMIT;
```

At lower isolation levels, you could have this race condition:
- Transaction 1 reads Account A balance: $1000, proceeds to transfer $600
- Transaction 2 reads Account A balance: $1000, proceeds to transfer $600
- Both transactions commit
- Account A ends up with -$200 (if no constraint) or one transaction succeeds and the other should have failed

At Serializable, this is impossible:
- The database ensures transactions execute as if they ran sequentially
- If Transaction 1 commits first, Transaction 2 will see the updated balance or will be aborted
- No race condition can occur

The consistency guarantee here is absolute: the outcome is exactly as if transactions ran one at a time. This is what you need for financial transactions where even a single incorrect operation is unacceptable. You're trading performance (more locks, possible retries) for correctness guarantees. In banking, this is the right trade-off, you can't afford lost updates or race conditions when dealing with money.

**Implementation:**
- **Strict locking approach:** Uses range locks, gap locks, or predicate locks in addition to row locks. Prevents phantoms by locking not just existing rows but also gaps where new rows could be inserted. Two-Phase Locking (2PL) protocol: acquires all locks before releasing any.
- **Serializable Snapshot Isolation (SSI):** Used by PostgreSQL and others. Tracks read/write patterns across transactions and detects conflicts. If a conflict is detected that could violate serializability, one transaction is aborted. More optimistic than strict locking.

## Real-World Comparison: Movie Seats vs TV Sales

Let's see two real-world scenarios that require different approaches.

### Booking Movie Seats

When you book a specific seat like Row 5, Seat 12:

```sql
BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;

SELECT status FROM seats WHERE row = 5 AND seat = 12 FOR UPDATE;
-- Returns: 'available'

UPDATE seats SET status = 'booked', customer_id = 123 
WHERE row = 5 AND seat = 12;

COMMIT;
```

Serializable works well here because you're targeting a specific row. Row-level locks prevent conflicts. Two people can't book the same seat. Different seats can be booked simultaneously, so concurrency remains reasonable.

You get the seat you selected, or you're immediately told it's unavailable. No surprises.

### Ordering a TV During Sales

When ordering from inventory during high-volume Black Friday sales:

```sql
BEGIN TRANSACTION ISOLATION LEVEL READ COMMITTED;

SELECT quantity FROM products WHERE product_id = 'TV-XYZ-123';
-- Returns: 50

-- User places order, inventory gets decremented

COMMIT;
```

Read Committed is often used because you're checking an inventory counter, not a specific item. High concurrency is critical with thousands of simultaneous orders. Using Serializable would create bottlenecks.

The trade-off: you add the item to cart and start checkout, but by the time you reach payment, inventory might be exhausted. You get the "Sorry, out of stock" message.

**The key difference:**

Movie seats are discrete items with row-level locking, so high isolation works. TV inventory is a shared counter with high contention, requiring lower isolation for performance.

To handle the TV scenario better, systems often reserve inventory temporarily when added to cart, implement queue systems, use optimistic concurrency control, or show real-time inventory updates.

## Additional Implementation Notes

### Optimistic Concurrency Control

Some systems use optimistic concurrency control, which assumes conflicts are rare. Transactions read data without acquiring locks, make changes locally, then at commit time check if anyone else modified the data. If a conflict is detected, the transaction aborts and retries.

This approach works well when conflicts are genuinely rare, as it avoids the overhead of lock management. Used in some NoSQL databases and application-level frameworks.

### MVCC Benefits

MVCC (Multi-Version Concurrency Control) provides significant benefits over traditional locking:
- Higher concurrency since readers don't block writers
- Fewer deadlocks since fewer locks are needed
- Consistent snapshots without holding locks

However, MVCC requires garbage collection of old versions and can use more storage space.

## Choosing the Right Level

| Scenario | Recommended Level | Why |
|----------|------------------|-----|
| Product catalogs | Read Committed | Fresh data, no cross-read consistency needed |
| Reports | Repeatable Read | Consistent snapshot required |
| Financial transactions | Serializable | Zero tolerance for anomalies |
| High-volume writes | Read Committed | Performance critical |
| Specific item inventory | Serializable | Prevent double-booking |
| Counter-based inventory | Read Committed + app logic | Balance performance and consistency |

## Sources

- [A Critique of ANSI SQL Isolation Levels](https://www.microsoft.com/en-us/research/publication/a-critique-of-ansi-sql-isolation-levels/) - Berenson et al., 1995
- [PostgreSQL Documentation: Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html)
- [MySQL InnoDB Locking](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking.html)
