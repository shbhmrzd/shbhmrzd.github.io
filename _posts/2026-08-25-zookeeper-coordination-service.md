---
layout: post
title: "ZooKeeper: A Coordination Service Built From a Handful of Primitives"
date: 2026-08-25
categories: [distributed-systems, coordination]
tags: [zookeeper, coordination, distributed-locks, group-membership, zab]
paper_title: "ZooKeeper: Wait-free coordination for Internet-scale systems"
paper_url: "https://www.usenix.org/legacy/event/atc10/tech/full_papers/Hunt.pdf"
paper_source: "Hunt et al., USENIX ATC 2010"
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2Fdistributed-systems%2Fcoordination%2F2026%2F08%2F25%2Fzookeeper-coordination-service.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# ZooKeeper: A Coordination Service Built From a Handful of Primitives

I kept running into ZooKeeper as a dependency of other systems - Hadoop uses it, Kafka used it for years to elect its controller and hold cluster metadata, and plenty of internal services lean on it for leader election. To understand how ZooKeeper helps solve these coordination problems, I read the original [ZooKeeper paper](https://www.usenix.org/legacy/event/atc10/tech/full_papers/Hunt.pdf) from 2010. These are my notes from that read.

The paper describes how ZooKeeper’s data model, API, and ordering guarantees can be used to implement configuration management, group membership, locks, and barriers. This post walks through the primitives and recipes described in the paper.

If you want the deeper version of any of this, read the paper. It is short and very readable.

---

## Coordination problems in the paper

The paper covers six common coordination problems: configuration management, rendezvous between processes, group membership, exclusive locks, read/write locks, and double barriers. Some operations read the latest state, while others wait for another process to change it.

ZooKeeper provides a common set of data and ordering primitives for all six. The coordination logic is implemented by the client library using znodes, watches, and conditional updates.

---

## What ZooKeeper is

A ZooKeeper deployment is an **ensemble** - a group of servers, usually an odd number like 3, 5, or 7. One server acts as the **leader** and the rest are **followers**. Write operations are ordered by the leader and replicated using ZooKeeper Atomic Broadcast, or ZAB. Reads are served locally by whichever server the client is talking to. More on why that split matters later.

![ZooKeeper ensemble with one leader, two followers, and a full data-tree replica on each server. Each client connects to one server.](/assets/img/zookeeper/ensemble.svg)

*A ZooKeeper ensemble. Each server holds a replica of the data tree, and clients connect to individual servers.*

Clients talk to ZooKeeper through a client library. When a client first connects, ZooKeeper establishes a **session** and hands back a session handle that the client uses for all future communication.

Each session has a configurable timeout. If ZooKeeper does not hear a request or a heartbeat from the client within the timeout, it treats the client as faulty and ends the session. A session can end two ways:

- The client explicitly closes the session handle.
- ZooKeeper does not receive anything from the session for longer than the session timeout.

ZooKeeper keeps a persistent, bidirectional TCP connection between client and server. If that connection breaks, say from a network blip, the client can reconnect to any other server in the ensemble and resume with the same session ID, as long as the session has not already expired. This detail matters later, because it is the channel ZooKeeper uses to push notifications back to clients.

---

## The data model

ZooKeeper stores everything in an in-memory tree of nodes called **znodes**. Its namespace looks like a file system.

![Znode hierarchy: the root has children /a and /b; /a has children /a/a1 and /a/a2.](/assets/img/zookeeper/data-model.svg)

You address a znode with a Unix-style path. The leftmost leaf above is `/a/a1`. Every server holds a full copy of this tree in memory, which is what lets reads be served locally without talking to anyone else.

Znodes hold small pieces of metadata and configuration. The default cap is 1 MB per znode, and it is configurable. Every znode also carries timestamps and version counters. The version counter makes conditional writes possible, which I will get to.

---

## Znode types

The paper defines two kinds of znodes a client can create.

**Regular znodes.** These can have data and can have child znodes. Once created, a regular znode sticks around until a client explicitly deletes it.

**Ephemeral znodes.** These are tied to the session that created them. The client can delete an ephemeral znode explicitly, or ZooKeeper removes it automatically when the creating session ends. Ephemeral znodes cannot have children. This gives the recipes a way to represent an active session in the data tree.

On top of either type, a client can set the **sequential** flag when creating a znode. ZooKeeper then appends a monotonically increasing number to the name, drawn from a counter on the parent.

Here is a concrete example. Under a parent `/p`, we create a first child with the sequential flag:

```
create("/p/node-", data="data1", sequential=true)
  -> creates /p/node-0000000001

create("/p/node-", data="data2", ephemeral=true, sequential=true)
  -> creates /p/node-0000000002  (ephemeral)
```

Real ZooKeeper zero-pads the counter to ten digits, so the generated name is `node-0000000001`. Sorting these names as strings preserves their numeric order, which the lock recipes use later. I will use the short form `node-1`, `node-2` from here on.

Once the client session that created `/p/node-2` expires, `/p/node-1` stays (it is regular) but `/p/node-2` disappears (it is ephemeral). The ordering the sequential flag gives you is what makes fair locking possible without a stampede.

---

## Watches

Znodes often hold configuration that many clients read. Polling the znode on every use adds load to the ensemble, so clients cache the value locally and use a watch to learn when that value becomes stale.

ZooKeeper's answer is **watches**. A client reads a znode with the watch flag set to true. ZooKeeper returns the current data and registers a one-time watch on that znode. The server sends a notification when the information returned by that read changes. The notification travels over the persistent TCP connection held by the client.

Three things to be careful about:

- A watch is a **one-time trigger**. Once it fires, or the session closes, it is gone. If the client wants to keep watching, it has to re-read with the watch flag set again.
- A watch tells you *that* something changed, not *what* changed. To get the new value, the client issues another read.
- Watch notifications can be delayed while the client is disconnected. ZooKeeper sends session events so that the client knows it should treat its cached state carefully until it reconnects.

While connected, the client uses the cached value until its watch fires. It reads the value again and sets a new watch after each notification.

---

## Client API

The paper describes the following API operations.

**`create(path, data, flags)`** creates a znode at `path`, stores `data` in it, and returns the name of the new znode. The flags pick the type (regular or ephemeral) and whether to set the sequential flag.

**`exists(path, watch)`** returns true if the znode exists and false otherwise. The watch can notify the client when the znode is created, deleted, or its data changes.

**`getChildren(path, watch)`** returns the names of the children of a znode.

**`getData(path, watch)`** fetches a znode's data along with its metadata, including the version counter. The watch flag works like it does for `exists`. One thing to note: if the path does not exist, `getData` does not set a watch, whereas `exists` does (that is how you wait for something to be created).

**`setData(path, data, version)`** writes data when the supplied `version` matches the znode's current version. A successful write increments the version, so other writes carrying the old version fail. This provides a compare-and-set operation across clients.

Every API has a **synchronous** and an **asynchronous** form. The synchronous form blocks until the operation completes. The asynchronous form lets a client have many outstanding operations while it does other work.

APIs also split into **reads** and **writes**. `create`, `delete`, and `setData` update state. `exists`, `getData`, and `getChildren` are reads. The split lines up with the architecture: all state-updating requests are served by the leader, while all reads are served locally by whichever replica the client is connected to.

---

## Ordering Guarantees

The recipes in the paper rest on two ordering guarantees.

**Linearizable writes.** All requests that modify ZooKeeper state are serialized into one global order, and that order respects precedence: if request 1 completes before request 2 is submitted, request 1 comes first. Every server applies writes in that same order.

The paper calls its definition **A-linearizability** (asynchronous linearizability). It allows a client to have multiple operations outstanding while preserving FIFO order for that client's requests. This is what allows the 5,000 configuration writes below to be pipelined.

**FIFO client ordering.** Requests from a single client are executed in the order that client sent them. This also applies when the client pipelines multiple asynchronous requests. The client library invokes their callbacks in order.

The recipes combine these guarantees with ephemeral and sequential znodes and watches.

---

## Wait-Free Operations

The paper's title is "Wait-free coordination." Here, **wait-free** means a ZooKeeper operation does not wait for another client to take some action. For example, the API has no lock-acquisition call that remains blocked until the current lock holder releases it. Network failures or the loss of a quorum can still delay or fail an operation.

Some coordination recipes still require a client to wait. In the lock recipe, a client reads the current state, sets a watch, and waits locally for a notification. The queueing logic remains in the client, while ZooKeeper stores the znodes and delivers ordered notifications.

---

## Recipe: batched configuration update

Now let's put the APIs and guarantees together on a real problem. A system elects a leader to manage a pool of worker processes. When the new leader takes over, it needs to update a batch of configuration and then let the workers know, but only once every change is done. Two requirements fall out of this:

1. While the leader is updating config, no worker should read a half-updated set.
2. If the leader dies partway through, no worker should use the incomplete config.

The mechanism is a **ready** znode used as a marker. Workers only trust the config if the ready znode exists. The leader does this:

1. Delete the ready znode if it exists.
2. Update all the config znodes (these can live in ZooKeeper at known paths).
3. Create the ready znode last, to signal completion.

Why this is correct comes straight from the FIFO client ordering guarantee. Every request comes from the same client, the leader. A worker that sees the new ready znode also sees all the config updates that came before it. If the leader crashes before creating ready, workers know that the configuration has not been finalized.

There is one more race to handle. A worker may see the old ready znode just before the new leader deletes it, and then start reading configuration while it is being changed. The worker must read ready with a watch. ZooKeeper guarantees that the worker receives the notification for the deletion before it can observe any of the configuration state written after that deletion. The worker can then stop using the configuration until ready is created again.

![Animation of a completed configuration update: the leader deletes ready, workers receive the watch notification and wait, the leader updates the configuration znodes, and recreates ready so workers can read the completed configuration.](/assets/img/zookeeper/configuration-update.gif)

*Completed update. Three configuration znodes are shown to illustrate the batch; the leader creates ready after all updates.*

![Animation of a leader crash during a configuration update: only part of the configuration is updated, ready remains absent, and workers continue waiting.](/assets/img/zookeeper/configuration-update-failure.gif)

*Leader failure during the batch. The ready marker remains absent, so workers do not use the incomplete configuration.*

Those config updates can be pipelined asynchronously. The paper gives the concrete numbers: a change operation has a latency on the order of 2 milliseconds, so a leader that updates 5,000 config znodes takes about 10 seconds when it sends them one after another. The same updates finish in under a second when issued asynchronously. FIFO client ordering is preserved while the writes are outstanding.

### Reads can be stale

There is a trap in this recipe when clients use ZooKeeper alongside their own side channels. Suppose two clients A and B share config in ZooKeeper and also talk directly to each other over their own channel. A changes the config in ZooKeeper, then tells B over the side channel. B re-reads the config from ZooKeeper and expects to see the change.

Reads are served locally by whichever replica B is connected to. If B's replica is slightly behind A's, B might not see the new config yet. The direct message outran the replication.

How does B make sure it sees A's change? One option is for B to issue a small **write** to ZooKeeper before reading. A's completed write precedes B's write in the global write order. FIFO client ordering then places B's read after its own write, so the read includes A's change.

ZooKeeper provides the **`sync`** request for this case. `sync` waits for the updates that were pending when it started to reach the server handling B's session. B follows it with a read. The paper calls this combination a **slow read**. It gives B a view that includes A's earlier change without adding another write.

---

## Recipe: rendezvous

The paper uses a master and a set of workers launched by a scheduler. The scheduler decides where the processes run, so their addresses and ports are not known ahead of time.

The client creates a rendezvous znode `/zr` and passes its path to the master and workers as a startup parameter. When the master starts, it writes its address and port into `/zr`. Workers read `/zr` with the watch flag set. A worker that starts before the master waits for a notification that `/zr` has been filled in.

The client can create `/zr` as an ephemeral znode. The master and workers watch for its deletion and clean themselves up when the client's session ends.

![Rendezvous animation: the client creates /zr, workers read it with a watch, the master writes its address and port, and workers read those details and connect.](/assets/img/zookeeper/rendezvous.gif)

---

## Recipe: group membership

The paper's group-membership recipe uses ephemeral nodes to represent processes whose sessions are still active.

Designate a znode `/zg` to represent the group. When a process starts, it creates an ephemeral child under `/zg`. A process with a unique identifier uses it as the child name. Otherwise, it sets the sequential flag and lets ZooKeeper assign a unique name. The child can store process information such as its address and port.

Once its child exists, a process continues with its work. If it shuts down cleanly, closes its session, or remains disconnected beyond the session timeout, ZooKeeper removes its ephemeral node. A crashed process can remain in the group until its session expires.

A process reads the group by calling `getChildren("/zg")`. To monitor members joining or leaving, it sets a watch on `/zg` and refreshes the group information when the watch fires. The next `getChildren` call sets a new watch.

![Group membership animation: processes register ephemeral children; after a process crashes, its child remains until session expiration, then a watching process refreshes the membership list.](/assets/img/zookeeper/group-membership.gif)

---

## Recipe: a simple lock and its limitations

Distributed locks coordinate access to a shared resource across machines. The simplest version is a lock-file approach with one znode.

To acquire the lock, a client tries to `create` a designated znode with the ephemeral flag. If the create succeeds, the client holds the lock. If the znode already exists, someone else holds it, so the client reads the znode and sets a watch to be notified when it is freed.

The holder releases the lock by deleting the znode. ZooKeeper also deletes it when the holder's session expires. Waiting clients receive a notification and try to create the lock znode again.

The paper points out two limitations with this recipe.

The first is the **herd effect**. Imagine a crowd of clients all waiting on the lock. When it is released, every one of them gets the notification and rushes to create the znode at once. Only one can win, but all of them woke up and contended for it. That is a lot of wasted work and it gets worse as the crowd grows.

The second is that this only gives you exclusive locking. If you want a read/write lock, where many readers can hold the lock at once but a writer needs exclusive access, this design cannot express it.

![Simple lock animation: releasing the lock wakes both waiting clients; both attempt create, one succeeds, and the other waits again.](/assets/img/zookeeper/simple-lock.gif)

---

## Recipe: a lock without the herd effect

The next recipe orders the clients and wakes one of them when the lock is released.

Define a lock znode `/zl`. Every client that wants the lock creates a child under `/zl` with both the **ephemeral and sequential** flags. ZooKeeper orders the requests using the sequence numbers assigned to these children.

Walk through it from one client's point of view. Client 3 wants the lock. It calls:

```
create("/zl/lock-", ephemeral=true, sequential=true)
  -> "/zl/lock-3"
```

Two other clients have already created `/zl/lock-1` and `/zl/lock-2`, so they are ahead in line. `lock-1` holds the lock and `lock-2` is waiting. Creating a znode adds the client to the queue. The client holds the lock once its znode has the lowest sequence number.

![Lock queue under /zl: lock-1 holds the lock, lock-2 watches lock-1, and lock-3 watches lock-2.](/assets/img/zookeeper/queued-lock.svg)

So client 3 does this:

1. Call `getChildren("/zl")` with watch false to list the children.
2. If `lock-3` is the lowest number in the list, the client holds the lock. Do the work, then release by deleting `lock-3`.
3. If there are lower-numbered znodes, find the one just before it - `lock-2` - and call `exists("/zl/lock-2", watch=true)`.
4. If `exists` returns false, the preceding znode disappeared between steps 1 and 3. Go back to step 1. Otherwise, wait for its deletion notification.

Each znode is watched by the client immediately behind it, so one client wakes up when a lock is released.

When the watched node `lock-2` disappears, client 3 gets notified and starts again from `getChildren`. It acquires the lock when `lock-3` has the lowest sequence number.

The client releases the lock by deleting its own znode. ZooKeeper deletes the znode if the client's session expires. Since every lock request is represented by a child of `/zl`, listing the children also shows the clients waiting for the lock.

![Predecessor-watch animation: deleting lock-1 wakes client 2; it rechecks and acquires the lock. Client 3 stays waiting until lock-2 is deleted, then rechecks and acquires.](/assets/img/zookeeper/queued-lock.gif)

---

## Recipe: read/write locks without the herd effect

The paper extends the same recipe to read and write locks.

The rules:

- A **write lock is exclusive**. While a process holds a write lock, no other process can hold a read or write lock. During a write the data is being modified, so concurrent access would give inconsistent or stale reads.
- **Read locks are shared**. Reads do not modify data, so many processes can hold read locks at the same time and read in parallel. But while any read lock is held, no write lock can be acquired. A writer must wait for all current readers to release.

Readers can run concurrently until a lower-numbered write request is present. Writers wait for every lower-numbered request.

The implementation extends the previous lock recipe. It uses the same lock znode `/zl` and ephemeral-sequential children. Read requests are prefixed `RL-` and write requests `WL-`.

![Read/write lock queue under /zl: WL-1 holds the write lock; RL-2 and RL-3 watch WL-1; WL-4 watches RL-3.](/assets/img/zookeeper/read-write-lock.svg)

For either lock type, the client first creates its ephemeral-sequential znode. It then follows the relevant procedure below.

**Acquiring a write lock** (client created `WL-3`):

1. Call `getChildren("/zl")`.
2. If your write znode has the lowest sequence number of all children, you hold the write lock.
3. Otherwise, find the child with the largest sequence number lower than yours and call `exists` on it with watch true.
4. If `exists` returns false, return to step 1 immediately. If it returns true, wait for the deletion notification and then return to step 1.

A writer waits behind everything ahead of it, reader or writer, because a write must be fully exclusive.

**Acquiring a read lock** (client created `RL-3`):

1. Call `getChildren("/zl")`.
2. You only need to make sure there is no **write** znode with a lower sequence number blocking you. If the only lower-numbered nodes are reads, you can grab the read lock alongside them.
3. If there is a lower-numbered write znode, call `exists` with watch true on the write znode with the highest sequence number below yours.
4. If `exists` returns false, return to step 1 immediately. If it returns true, wait for the deletion notification and then return to step 1.

A write lock watches the znode immediately before it, whether that znode represents a read or a write. A read lock watches the closest lower-numbered write znode because earlier read locks can run concurrently.

Deleting a write znode can notify several readers. All of them may now hold the read lock, so waking them together is the intended behavior. The herd effect applies when many clients wake up and only one can proceed.

The client releases either lock by deleting its znode. Session expiration provides the same cleanup as in the exclusive-lock recipe.

![Read/write lock animation: the first writer releases, two readers proceed together, and the next writer waits until both earlier readers have finished.](/assets/img/zookeeper/read-write-lock.gif)

---

## Recipe: double barrier

A double barrier synchronizes both the start and the end of a computation. The paper represents the barrier with a znode `/b` and defines a threshold for the number of participating processes.

Each process creates a child under `/b` when it enters. The process that satisfies the threshold creates `/b/ready`. Other processes watch for that znode and begin their work after it appears.

When a process finishes, it deletes its child from `/b`. Processes leave the barrier after all participant znodes have been removed. They watch particular child znodes and check the exit condition when those children disappear. This spreads the watches across the participant znodes instead of having every process watch the same child.

![Double-barrier animation: processes register and wait for ready, begin computing once the entry condition is met, remove their children on completion, and leave after all participant children are gone.](/assets/img/zookeeper/double-barrier.gif)

---

## The architecture underneath

The next part of the paper explains how ZooKeeper provides these guarantees.

ZooKeeper runs as an ensemble for high availability. Every server holds a complete replica of the data tree in an in-memory database. Each znode can store up to 1 MB by default, configurable. For durability, each update is written to disk before it is applied to the in-memory database. A client connects to exactly one server to submit requests.

### How requests are processed

Every server can handle requests, but how a request is handled depends on whether it is a read or a write. Servers process each client's requests in FIFO order.

**Reads** are answered from the server's local in-memory database. They avoid the agreement protocol and disk activity used by writes.

![Read path: the client sends a request to its server, the server reads its local in-memory replica, and replies with the result and zxid.](/assets/img/zookeeper/read-path.svg)

Each read is tagged with the **zxid** (ZooKeeper transaction ID) reflecting the last transaction that server has seen. This local read path is why ZooKeeper does so well on read-heavy workloads.

**Writes** are more involved because they change state across the whole ensemble. When a server receives a write, it forwards the request to the leader, since only the leader assigns the global write order. The leader validates it, turns it into a transaction, and broadcasts it. The whole path looks like this:

![Write path: the server forwards the request to the leader, which prepares and broadcasts a transaction. A quorum logs and acknowledges it; the transaction commits, and the receiving server applies it and replies.](/assets/img/zookeeper/write-path.svg)

A few details on that path. The leader validates first - for a conditional `setData`, it checks the supplied version against the znode's current version. If valid, it turns the request into a transaction, for example a `setData` transaction carrying the new data, version, and timestamp. If there is an error, like a version mismatch or a missing znode, it generates an error transaction instead.

To propagate the update, the leader uses the **ZooKeeper Atomic Broadcast protocol (ZAB)**. It sends the transaction to the followers as a proposal. A **majority quorum** must acknowledge the proposal before it commits - at least f+1 servers in a 2f+1 ensemble. That threshold lets ZooKeeper tolerate up to f server failures while retaining a quorum. A follower that is behind can apply the committed transaction later while catching up.

ZAB gives two ordering properties that back the guarantees from earlier: changes broadcast by a leader are delivered in the order they were sent, and all changes from previous leaders are delivered to a newly established leader before it broadcasts any of its own.

Writes also drive watch notifications. If a write changes information covered by an active watch, the server connected to the watching client sends the notification and clears the watch. Transaction application and client events are ordered, so a client sees the watch event before it sees the new state that caused the event. The server a client is connected to manages that client's watches.

### Durability: write-ahead log and fuzzy snapshots

Each server keeps its full replica in memory, which is what lets it serve reads locally. To survive crashes, each server writes every update to a **write-ahead log** on disk before applying it to the in-memory database. This logging is what lets updates survive a crash. ZooKeeper reuses this same log as the record of ZAB proposals, so it does not have to write each change to disk twice.

Replaying the entire log to recover would be slow for a long-running server, so ZooKeeper periodically takes a **snapshot** of the in-memory database. On restart, a server loads the latest snapshot and replays only the transactions since that snapshot began, which restores the database quickly.

These snapshots are **fuzzy**. ZooKeeper takes them without locking the database. It performs a depth-first scan and atomically reads each node's data and metadata. The resulting snapshot can contain a mix of old and new values that never existed together at a single instant.

Here is the paper's own example. Two znodes `/foo` and `/goo` start at values `f1` and `g1`, both version 1, when the snapshot begins. Then three transactions arrive while the scan is running:

![Fuzzy snapshot timeline: the scan reads /goo at g1, three transactions update /foo to f2, /goo to g2, and /foo to f3, then the scan reads /foo at f3. The snapshot combines f3 and g1.](/assets/img/zookeeper/fuzzy-snapshot.svg)

After all three transactions, the true state is `/foo = f3, v3` and `/goo = g2, v2`. But the fuzzy snapshot recorded `/foo = f3, v3` and `/goo = g1, v1`, a combination that was never valid at any moment.

Recovery works because ZooKeeper transactions are **idempotent**. On restart, ZooKeeper loads the fuzzy snapshot and replays all transactions delivered since the snapshot began. The leader first computes the result of a write and then broadcasts a transaction containing absolute values, such as a `setDataTXN` with the new data, version, and timestamps. Replaying `setData /goo -> g2, v2` over the snapshot's `g1, v1` corrects the stale value. Replaying the same transaction over `g2, v2` leaves the result unchanged.

### Client session management

ZooKeeper detects dead clients with timeouts. If no server hears from a client within the session timeout, the leader declares the session dead. Clients keep sessions alive by sending requests, or heartbeats when they have nothing else to send.

The timing is worth noting. When a session has been idle for one third of its timeout, the client library sends a heartbeat. If the client does not hear from the server for two thirds of the timeout, it switches to another server. Responses and heartbeats carry the last zxid seen by the server.

When a client connects to a new server, that server compares the client's last zxid against its own. If the client has seen a more recent transaction than the new server has, the server waits until it catches up before re-establishing the session. Because updates are replicated to a majority of servers, the client is guaranteed to find a server with a recent enough view of the data. This is what stops a client from silently going back in time after a reconnect.

---

## What the numbers look like

The design is built for reads, and the paper's benchmarks show it. The setup: a Java server logging to one dedicated disk and snapshotting to another, driven by 250 simulated clients spread across 35 machines, each client keeping at least 100 requests outstanding, every request reading or writing 1 KB of data. They saturated the system and varied the read/write mix and the number of servers.

Here is Table 1 from the paper, throughput in operations per second at the two extremes, all reads versus all writes:

| Servers | 100% reads | 0% reads (all writes) |
|---|---|---|
| 3 | 87,000 | 21,000 |
| 5 | 165,000 | 18,000 |
| 7 | 257,000 | 14,000 |
| 9 | 296,000 | 12,000 |
| 13 | 460,000 | 8,000 |

Two things stand out in this table.

First, read throughput ranges from roughly four times write throughput with three servers to more than fifty times with thirteen servers. Reads are answered from local memory, while writes go through atomic broadcast and are logged to disk before they are acknowledged.

Second, in this benchmark, adding servers increases read throughput and reduces write throughput. More replicas can serve reads locally, so read throughput climbs from 87k at three servers to 460k at thirteen. Writes require atomic broadcast and quorum acknowledgements, and their throughput drops from 21k to 8k as the ensemble grows.

This is also the evidence behind "ZooKeeper works best in read-heavy workloads." The paper states its target as a 2:1 to 100:1 read-to-write ratio, at which it handles tens to hundreds of thousands of operations per second. Coordination traffic sits squarely in that range - lots of "who is the leader, who is in the group, what is the config", and comparatively few changes to any of it.

---

## Closing thoughts

What stuck with me after reading the paper is how far a small design goes. Every server holds the whole tree in memory for fast local reads. ZAB orders writes across the ensemble. Write-ahead logs and fuzzy snapshots preserve state across crashes while the service continues processing requests. The client API and its ordering guarantees are enough to implement configuration management, rendezvous, group membership, locks, and double barriers.

The recipes reuse three ideas: ephemeral nodes represent sessions, sequential nodes impose an order, and watches let clients wait without polling. Once those clicked, the recipes became much easier to follow.

---

## Sources

- [Hunt et al., 2010. ZooKeeper: Wait-free coordination for Internet-scale systems](https://www.usenix.org/legacy/event/atc10/tech/full_papers/Hunt.pdf) - USENIX ATC 2010
- [Apache ZooKeeper documentation](https://zookeeper.apache.org/doc/current/index.html)
