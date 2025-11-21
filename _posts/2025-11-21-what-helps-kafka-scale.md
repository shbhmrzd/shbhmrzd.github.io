# How does kafka scale for log processing ?
I recently went back to reading the original Kafka white paper from 2010.

At the time, other existing messaging solutions like IBM WebSphere MQ had transactional support that allowed an 
application to insert messages into multiple queues atomically. The focus was on rich set of delivery guarantees leading
to complexity in the APIs and their implementations, and couldn't handle log processing at scale.

Kafka took a different approach. **Throughput over complexity**.

Most of us know the standard architectural choices that make Kafka fast by virtue of these being part of 
Kafka APIs and guarantees
- `Batching`: Grouping messages during publish and consume to reduce `TCP/IP` round trips.
- `Pull Model`: Allowing consumers to retrieve message at a rate they can sustain
- `Single consumer per partition per consumer group`: All messages from one partition are consumed only by a single consumer per consumer group. If Kafka intended to support multiple consumers to simultaneously read from a single partition, they would have to coordinate who consumes what message requiring locking and state maintenance overhead.
- `Sequential I/O`: No random seeks, just appending to the log.

I wanted to further highlight **two other optimisations** mentioned in the Kafka white paper which are not evident to 
daily users of Kafka but are interesting hacks by the Kafka developers

## Bypassing the JVM Heap using File System Page Cache
Kafka avoids caching messages in the application layer memory. Instead, it relies entirely on the underlying file system
page cache. This avoids double buffering and, reduces Garbage Collection (GC) overhead.
If a broker restarts, the cache remains warm because it lives in the OS, not the process. Since both the producer and 
consumer access the segment files sequentially with the consumer often lagging the producer by a small amount, normal 
operating system caching heuristics are very effective (specifically write-through caching and read-ahead).

## The "Zero Copy" Optimisation
Standard data transfer is inefficient. To send a file to a socket, the OS usually copies data 4 times 

**(Disk -> Page Cache -> App Buffer -> Kernel Buffer -> Socket)**.

Kafka exploits the Linux `sendfile API` [Java’s FileChannel.transferTo](https://stackoverflow.com/questions/16451642/why-is-java-nio-filechannel-transferto-and-transferfrom-faster-does-it-us)
to transfer bytes directly from the file channel to the socket channel.
This cuts out 2 copies and 1 system call per transmission.


Read the entire paper at : https://lnkd.in/gqvs85uX