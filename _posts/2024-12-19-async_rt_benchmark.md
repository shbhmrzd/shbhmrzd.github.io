# Performance Comparison: Tokio vs Tokio-Uring for High-Throughput Web Servers

This article documents an experiment comparing the performance of two Rust asynchronous runtimes, **Tokio** and **Tokio-Uring**, 
in hosting a high-throughput server. The objective is to measure and analyze the throughput of a TCP stream-based server 
where each request results in publishing an event to Kafka. The server runs on a 2-core machine.

---

## Benchmark Setup

### System Specifications
- **Operating System**: Ubuntu 22.04.1 LTS
- **Kernel Version**: 6.8.0-1018-gcp
- **CPU**: Intel(R) Xeon(R) CPU @ 2.20GHz
- **Cores**: 2
- **Total Memory**: ~4GB

### Test Workflow
For each incoming request:
1. Accept the payload via a TCP stream.
2. Parse the HTTP request to extract the payload.
3. Publish the payload to a Kafka cluster using a Kafka base producer.

### Test Parameters
- **Kafka Configuration**:
    - Configured batch size, linger.ms, compression type, and queue buffering to optimize for increased throughput and performance.
- **File Descriptor Limit**: Increased to 65,536 using `ulimit -n 65536`.
- Used a **remote Kafka cluster** to isolate the benchmark machine from external resource contention.
- **Benchmarking Framework**: The [Criterion](https://github.com/bheisler/criterion.rs) benchmarking framework was used.

---

## Results

| Runtime         | Lower Bound (Requests/Second) | Upper Bound (Requests/Second) |  
|------------------|-------------------------------|-------------------------------|  
| **Tokio**       | 4459.9 ops/sec                | 4656.2 ops/sec                |  
| **Tokio-Uring** | 3924.6 ops/sec                | 3939.5 ops/sec                |  


### Observations
- **Tokio** achieved slightly higher throughput than Tokio-Uring in handling TCP streams, despite Tokio-Uring's direct use of Linux's `io_uring` interface.
- **Tokio-Uring** showed instability under sustained high-throughput workloads. The application would stop accepting new connections, fail to assign ephemeral ports, and require a restart.

---

## Challenges with Tokio-Uring

1. **Lack of HTTP Frameworks**:
    - Tokio-Uring is incompatible with popular HTTP frameworks like Hyper, and no widely used alternatives exist for it.
    - For benchmarking, we used **TCP streams** directly for both Tokio and Tokio-Uring to ensure fairness.

2. **Instability Under Load**:
    - Under sustained high loads, Tokio-Uring occasionally stopped processing new requests and exhibited erratic behavior, such as stalling the application.

---

## Error Encountered: Using Hyper with Tokio-Uring

When attempting to use the `Hyper` framework on the Tokio-Uring runtime, the following runtime error was encountered repeatedly:

```bash
thread 'main' panicked at /root/.cargo/registry/src/index.crates.io-6f17d22bba15001f/tokio-1.42.0/src/task/local.rs:418:29:
`spawn_local` called from outside of a `task::LocalSet` or LocalRuntime
```

### Implications:
- This incompatibility is a major limitation for building web servers with Tokio-Uring, as existing HTTP frameworks cannot be used out of the box. Development would require low-level TCP handling.


## Key Challenges: Tokio-Uring

1. **Application Stalling**:
    - At high throughput, Tokio-Uring stops accepting new connections.

2. **TCP Connection Limit**:
    - Stalling prevents ephemeral ports from being assigned.

3. **Lack of HTTP Framework**:
    - No existing HTTP frameworks available for Tokio-Uring.
    - Forced to rely on low-level TCP streams.

## Implementation Links

- [Tokio Implementation](https://github.com/shbhmrzd/benchmark_tokio_uring/blob/main/io_uring_feedback/src/main_tcp_tokio_remote_kafka_base_producer.rs)
- [Tokio-Uring Implementation](https://github.com/shbhmrzd/benchmark_tokio_uring/blob/main/io_uring_feedback/src/main_tcp_remote_kafka_base_producer.rs)


---

## Conclusions

### **Key Findings**:
1. **Tokio**:
    - Achieved higher throughput (~4.5k req/sec).
    - Stable and scales well under load.

2. **Tokio-Uring**:
    - Requires debugging for stalling and connection issues.
