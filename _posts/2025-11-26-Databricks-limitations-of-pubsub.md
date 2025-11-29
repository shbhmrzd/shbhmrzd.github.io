# When Pub/Sub Is Not Enough: Thoughts on the Databricks Paper, Using Kafka as a Foil

A few months back, Databricks published a paper titled [“Understanding the limitations of pubsub systems”](https://sigops.org/s/conferences/hotos/2025/papers/hotos25-426.pdf).
The core thesis is that traditional pub/sub systems  suffer from fundamental architectural flaws that make them 
unsuitable for many real-world use cases. The authors propose “unbundling” pub/sub into an explicit durable store + a 
watch/notification API as a superior alternative.

The paper starts off by aptly summarising the different use cases of pub/sub systems. It later lists the limitations 
of using pub/sub systems in each of the use cases. In my opinion, some of the limitations are overstated, and certain implementation shortcomings 
by the users of pub/sub solutions are presented as architecture flaws of the system.

This article is my attempt to reconcile the paper’s critique with real-world Kafka experience.
I largely agree with the diagnosis for stateful replication and cache-invalidation scenarios, but I believe 
the traditional pub/sub model remains the right tool for workloads of high-volume event ingestion and real-time analytics.

## What the Paper Gets Right: Real Kafka Limitations

### 1. Partition Count Is an Exposed Implementation Detail That Bites
Kafka scales consumer groups by partitioning. The maximum parallelism of a consumer group is bounded by the number of 
partitions. Once you have 10,000 partitions, you can’t easily reduce them, and you’re stuck with that upper bound forever.
You cannot have dynamically sharded consumers that own arbitrary key ranges independently of partition boundaries. 

Kafka forces you to choose between “can’t scale past N consumers” or “pay massive overhead forever”.
This is a real architectural limitation, not just an implementation detail.

### 2. Garbage Collection of Old Messages Can Break Correctness
Kafka will eventually delete or compact data. Full-stop.

With time-based or size-based retention, messages disappear even if a consumer is down for weeks.
With log compaction, only the latest value per key is kept. If your logic is stateful and requires every event 
(e.g. “user changed plan from Basic → Pro → Enterprise”), compaction silently drops history, and you get wrong results with no warning.

The paper correctly notes that compaction defers, but does not eliminate, message loss. There is no built-in signal when
data a consumer needed has been cleaned up.

### 3. The Log Is Not Queryable
Kafka topics are append-only logs. We cannot run ad hoc SQL queries like “What is the current state of user 12345?” or 
“Give me all events for key X in the last hour” without external indexing (Kafka Streams state stores, ksqlDB, external databases, etc.).

For many stateful replication or cache-invalidation use cases, we end up bolting on RocksDB state stores or external 
databases anyway, which is exactly what the paper says we should do from the beginning.

### 4. Ordering Anomalies in Multi-Key Transactions
When replicating from a source database, Kafka can only guarantee order per partition. Cross-partition (i.e., cross-row) 
transactions can be reordered or applied with snapshot inconsistencies. The source database is the authority on transaction
order and atomicity. The pub/sub intermediary should not introduce a new partial order.

This is a real problem for point-in-time consistent replication.
   

## Where I Disagree: Not All Limitations Are Fundamental
Several criticisms in the paper are real pain points, but they are manageable or acceptable trade-offs rather than fatal flaws.

### 1. “Backlogs are indistinguishable from outages.”
The paper claims that because messages are delivered approximately in order, a huge backlog looks like silence.
This is misleading. Kafka exposes end-to-end lag metrics (consumer lag = current log end offset − consumer offset) per 
partition with sub-second granularity. Every mature team monitors lag, sets alerts at the 1-hour or 6-hour mark, and 
distinguishes “slow” from “dead” easily.

The issue could be organizational if the instrumentation is not done properly. But it’s not Kafka’s fault.


### 2. “Long consumer downtime → huge backlog → cache invalidation takes hours”
The paper gives a real horror story of a consumer down for days, leading to hours of catch-up.
Yes, that could happen. 
But for cache invalidation, the correct mitigation is log compaction + key-based topics. If invalidation messages are “invalidate key X” or “new version of key X is V”,
compaction means the backlog collapses to just the latest version per key. Catch-up becomes O(# distinct keys), not O(# events).


### 3. “Data eventually gets garbage-collected, causing correctness loss.”
Only if we treat Kafka as a source of truth with infinite history.
For the majority of real-time use cases like clickstream, metrics, tracing, audit logs, and sensor data, the value of an event
older than a few days is near zero. Stale data is not just useless, it’s harmful (in terms of storage cost, processing cost, and noise).
Accepting bounded retention is a feature, not a bug, for these workloads.


## Right Tool, Right Workload 

Kafka has real architectural limitations with consumer parallelism within a group being constrained by partitions, logs 
not natively queryable as general key‑value or SQL stores, and retention/compaction  silently dropping data that slow 
consumers never see. These limitations become painful when Kafka is used as a database or as a generic replication 
solution between stateful systems.

The Databricks “unbundled” design of an explicit store plus a watch API is a better fit for correctness‑critical state 
replication, cross‑store consistency, and strong cache invalidation, where the system must expose only states that 
actually existed at the source and provides clear semantics for lag and resynchronization.

Some workloads are about moving immutable events as fast as possible, others are about consistently mirroring state.
One tool cannot fit all use cases. 
The right answer, as usual, is **"Know thy requirements"**.
