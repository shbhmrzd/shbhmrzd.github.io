---
layout: post
title: "PR Review Guidelines: What I Look For in Code Reviews"
date: 2026-01-14
categories: [software-engineering, best-practices]
tags: [code-review, pull-requests, software-quality, engineering-practices]
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2F2026%2F01%2F14%2Fpr-review-guidelines.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# PR Review Guidelines: What I Look For in Code Reviews

These are notes from my personal checklist of things I look for while reviewing pull requests. I picked these up from comments I received on my own PRs from seniors, patterns I observed in other reviews, and lessons learned from production incidents that made me think "we should have caught this earlier."

This is not an exhaustive list, and I would not present it as strict doctrine. It's a general guideline I refer to. Obviously, there are cases when we trade off thorough reviews for POCs or hotfixes. Maybe this can help both code authors and reviewers as a checklist.

I do not intend to favor a particular paradigm (functional vs OOP), language, or framework. My objective is to summarize a generic guideline that works across most use cases.

---

## 1. Write in the Natural Style of the Language You Are Using

Every language has its own idioms and patterns i.e. a natural way of doing things. When you fight against these patterns by borrowing approaches from other languages or ecosystems, the code often ends up more verbose, harder to maintain, and sometimes less efficient.
When the code looks "native" to the language, tools like compilers, runtimes, and linters (Clippy) can recognize standard patterns more reliably. Compilers apply optimizations when they can prove certain properties about the code, idiomatic patterns make these properties more obvious.
Taking a few examples from Rust below.

### Rust: Prefer Iterators Over Manual Loops

Rust prefers iterators with `collect()`, `map`, and `filter` chains over manual loops with indexing when possible. 

Why iterators can be faster:
- **Bounds check elimination**: With manual indexing (`items[i]`), Rust must verify `i < items.len()` on every access. Iterators eliminate these runtime checks because the compiler knows they won't produce out-of-bounds indices.
- **Potential for vectorization**: Simple iterator chains like `map` and `filter` can make it easier for LLVM to recognize vectorizable patterns and apply SIMD optimizations. That said, LLVM can also vectorize simple loops, and this isn't a guaranteed win, but iterators don't hurt and often help.
- **Zero-cost abstraction**: In practice, simple iterator chains often compile to identical or better assembly than hand-rolled loops. Refer these for detailed analysis [How to avoid bounds checks in Rust](https://shnatsel.medium.com/how-to-avoid-bounds-checks-in-rust-without-unsafe-f65e618b4c1e) and [The Humble For Loop in Rust](https://blog.startifact.com/posts/humble-for-loop-rust/)

**Manual loop with indexing:**
```rust
let mut result = Vec::new();
let mut i = 0;
while i < items.len() {
    if items[i] > 10 {
        result.push(items[i] * 2);
    }
    i += 1;
}
```

**Idiomatic iterator chain:**
```rust
let result: Vec<_> = items
    .iter()
    .filter(|&x| *x > 10)
    .map(|x| x * 2)
    .collect();
```

The iterator version is more concise, eliminates index-out-of-bounds risks, and compiles to similar performance.

### Use Option/Result with `?` Instead of Nested Match

Using `Option`/`Result` with `?` instead of nested `match` keeps code flat and more readable.

The `?` operator is Rust's way of handling errors without pyramids of nested matches. It propagates errors up the call stack and keeps your code readable.
Deep nesting makes it harder to see the actual logic at a glance. The `?` operator flattens this into a linear flow that's easier for humans to understand (the compiler handles both patterns similarly).

**Without `?` operator (nested matches):**
```rust
fn read_user_config(user_id: UserId) -> Result<Config, Error> {
    match fetch_user(user_id) {
        Ok(user) => {
            match load_config_file(&user.config_path) {
                Ok(raw_config) => {
                    match parse_config(raw_config) {
                        Ok(config) => {
                            match validate_config(&config) {
                                Ok(_) => Ok(config),
                                Err(e) => Err(e),
                            }
                        }
                        Err(e) => Err(e),
                    }
                }
                Err(e) => Err(e),
            }
        }
        Err(e) => Err(e),
    }
}
```

**With `?` operator (flat and clear):**
```rust
fn read_user_config(user_id: UserId) -> Result<Config, Error> {
    let user = fetch_user(user_id)?;
    let raw_config = load_config_file(&user.config_path)?;
    let config = parse_config(raw_config)?;
    validate_config(&config)?;
    Ok(config)
}
```

The second version is easier to read and easier to modify. The compiler generates essentially the same code for both versions - the `?` operator is just syntactic sugar that desugars into the match-based early return.
The real win is human readability: each `?` clearly signals "if this fails, return the error; otherwise, continue." You can follow the happy path linearly without mentally tracking nested scopes.

**Same principle applies to Option:**
```rust
// Nested Option handling
fn get_user_email(db: &Database, user_id: UserId) -> Option<String> {
    match db.find_user(user_id) {
        Some(user) => match user.profile {
            Some(profile) => match profile.email {
                Some(email) => Some(email.clone()),
                None => None,
            },
            None => None,
        },
        None => None,
    }
}

// With ? operator
fn get_user_email(db: &Database, user_id: UserId) -> Option<String> {
    let user = db.find_user(user_id)?;
    let profile = user.profile.as_ref()?;
    let email = profile.email.clone();
    Some(email)
}
```

The `?` operator on `Option` returns early with `None` if the value is `None`, otherwise unwraps the value and continues.
Again, the compiler produces similar code for both patterns. The advantage is readability: the nested version forces you to track indentation levels and figure out which branch does what.
The `?` version lets you read straight down. If any step fails, we bail out and otherwise, we keep going. This matters when you come back to this code in six months or when a new team member needs to understand the error flow.

### What are some exceptions

Obviously, there are exceptions:
- **POCs and prototypes**: When you need speed of code delivery over style during exploration
- **Performance-critical code**: Sometimes you need manual control (but profile first!)
- **Interfacing with external systems**: Foreign APIs may force non-idiomatic patterns

---

## 2. Use Error Codes/Enums, Not String Messages

**The Principle:**  
Errors should be represented as structured types i.e. enums in Rust, error codes in Java, custom error classes in Python rather than arbitrary string messages.
Strings are for humans to read in logs whereas structured types are for code to handle.

### Why This Matters

When errors are just strings like `"Connection failed"` or `"Invalid request"`, you lose the ability to programmatically distinguish between different failure modes.

The problems compound quickly:
- **You can't handle specific cases**: How do you retry on timeout but alert on authentication failure when both are just strings?
- **String comparison is brittle**: Change "Database Error" to "DB Error" and you break downstream handlers
- **Observability becomes manual**: You end up grepping logs instead of querying structured metrics

### Rust Example: String Errors vs. Enum Errors

**String-based errors (fragile):**
```rust
fn connect_to_db(url: &str) -> Result<Connection, String> {
    if !is_valid_url(url) {
        return Err("Invalid database URL".to_string());
    }
    if !can_reach_host(url) {
        return Err("Cannot reach database host".to_string());
    }
    Err("Connection failed".to_string())
}

// Caller has to parse strings - brittle and error-prone
match connect_to_db(url) {
    Err(e) if e.contains("Invalid") => { /* handle bad URL */ },
    Err(e) if e.contains("reach") => { /* handle network issue */ },
    _ => { /* what about typos or message changes? */ }
}
```

**Enum-based errors (robust):**
```rust
#[derive(Debug)]
enum DatabaseError {
    InvalidUrl(String),
    NetworkUnreachable(String),
    AuthenticationFailed,
    ConnectionTimeout,
}

fn connect_to_db(url: &str) -> Result<Connection, DatabaseError> {
    if !is_valid_url(url) {
        return Err(DatabaseError::InvalidUrl(url.to_string()));
    }
    if !can_reach_host(url) {
        return Err(DatabaseError::NetworkUnreachable(url.to_string()));
    }
    Err(DatabaseError::ConnectionTimeout)
}

// Exhaustive pattern matching - compiler catches missing cases
match connect_to_db(url) {
    Ok(conn) => { /* use connection */ },
    Err(DatabaseError::InvalidUrl(url)) => {
        log::error!("Invalid URL: {}", url);
        // Return 400 to client
    },
    Err(DatabaseError::NetworkUnreachable(_)) => {
        // Fallback to replica, alert ops
    },
    Err(DatabaseError::AuthenticationFailed) => {
        // Refresh credentials
    },
    Err(DatabaseError::ConnectionTimeout) => {
        // Retry with exponential backoff
    },
}
```

The enum-based version gives you exhaustive checking at compile time. Add a new error variant and the compiler forces you to handle it everywhere. With strings, you'd silently fall through to a default case.

### But You Still Need Human-Readable Messages

Structured error types don't mean cryptic logs. You can have both structured handling and readable messages:

```rust
impl Display for DatabaseError {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        match self {
            DatabaseError::InvalidUrl(url) => 
                write!(f, "Invalid database URL: {}", url),
            DatabaseError::NetworkUnreachable(host) => 
                write!(f, "Cannot reach database host: {}", host),
            DatabaseError::AuthenticationFailed => 
                write!(f, "Database authentication failed"),
            DatabaseError::ConnectionTimeout => 
                write!(f, "Database connection timed out"),
        }
    }
}
```

Now when you log the error, you get readable output. But when you need to handle it programmatically, you match on the enum variant, not the string.

### The Observability Win

This is where structured errors really shine. When errors are just strings, you're stuck grepping logs and manually categorizing failures. I've spent too many incident reviews doing exactly that—searching for variations of "connection failed" vs "connection timeout" vs "connection refused" and trying to figure out patterns.

With error enums or codes, your observability stack gets structured data it can actually work with:

**Track metrics by error type:** Instead of counting generic "errors," you can track `database_errors{type="timeout"}` vs `database_errors{type="auth_failed"}`. This lets you see which specific failure mode is spiking. Is it network issues or bad credentials? You know immediately.

**Alert on specific failures:** You can set up precise alerts. Page the oncall when `PaymentErrorCode.FRAUD_DETECTED` spikes above threshold, but just log `CARD_EXPIRED` errors since those are user-fixable. With string errors, you're alerting on everything or nothing.

**Build error dashboards:** When each error type has a code, you can visualize error distribution by category over time. You might discover that 80% of your errors are actually one specific type that you can fix with a targeted improvement.

**Correlate with deployments:** Did the new release increase `GATEWAY_TIMEOUT` errors by 15%? With structured error codes, you can graph error rates by type before and after deployments. This makes rollback decisions data-driven instead of gut-feel.
 

### When String Errors Are Acceptable

Obviously, there are cases where structured errors are overkill:
- **Scripts and tools**: A quick automation script doesn't need full error enums
- **Propagating third-party errors**: Sometimes you're just forwarding an error message from an external API
- **Internal prototypes**: Speed over structure when exploring

But for production services, especially at API boundaries or between system components, structured error types are worth the upfront effort.

---

## 3. Structured Logging Over Print Statements

**The Principle:**  
Logs should be machine-parseable first, human-readable second. Use structured logging libraries that output JSON or key-value pairs, not `println!` or string concatenation.

### Why This Matters

Early in my career, I spent hours grepping through plain-text logs trying to answer simple questions like "How many requests failed with timeout errors in the last hour?" or "Which user IDs experienced this specific error?"

With unstructured logs like:
```
Error processing request for user 12345 - timeout after 30s
Failed to connect to database for user 98765
Error processing request for user 54321 - invalid input
```

You end up writing fragile regex patterns, the data isn't indexed, and you can't aggregate or alert on specific fields. Every question requires a new grep pattern and manual counting.

### Unstructured vs. Structured Logging

**Bad: String concatenation**
```rust
println!("User {} attempted payment of ${} at {}", 
    user_id, amount, timestamp);

log::error!("Payment failed for order {} - {}", order_id, error_msg);
```

**Good: Structured fields**
```rust
use tracing::{info, error};

info!(
    user_id = %user_id,
    amount = %amount,
    timestamp = %timestamp,
    "payment_attempted"
);

error!(
    order_id = %order_id,
    error_type = %error.code(),
    error_msg = %error,
    "payment_failed"
);
```

### The Observability Difference

With structured logs, your observability tools can:

**Query by field:**
```sql
SELECT COUNT(*) FROM logs 
WHERE error_type = 'TIMEOUT' 
  AND timestamp > now() - 1h
```

**Create dashboards:**
- Payment failure rate by `error_type`
- Top 10 `user_id` values experiencing errors
- P95 latency grouped by `endpoint`

**Set precise alerts:**
```
ALERT payment_fraud_spike
  IF rate(logs{error_type="FRAUD_DETECTED"}[5m]) > 10
```

### Context Matters: Log Levels

Structured logging works hand-in-hand with proper log levels:

- **ERROR**: Something failed and requires attention. Always include structured context about what failed and why.
- **WARN**: Something unexpected but recovered. Include context about what was unexpected.
- **INFO**: Key business events (user signup, payment processed). Structure helps with analytics.
- **DEBUG**: Detailed flow for troubleshooting. Can be verbose, but still structure the key fields.

**Example: Progressive detail with structured fields**
```rust
// INFO: Business event
info!(user_id = %user_id, order_id = %order_id, 
      amount = %amount, "order_completed");

// WARN: Unexpected but handled
warn!(user_id = %user_id, retry_count = %retries, 
      "payment_gateway_slow");

// ERROR: Failure requiring action
error!(user_id = %user_id, order_id = %order_id,
       error_code = %err.code(), error_msg = %err,
       "payment_processing_failed");
```

### When Print Debugging Is Fine

Look, I still use `println!` when:
- **Local development**: Quick debug of variable values
- **One-off scripts**: CLI tools that output to stdout
- **Prototyping**: When you're just exploring an idea

But once code hits a shared environment (staging, production), structured logging should be the default.

### Common Mistakes

**Mistake 1: Logging the same info at multiple levels**
```rust
// Don't do this
debug!("Processing payment");
info!("Processing payment");
// Pick one level based on importance
```

**Mistake 2: Logging sensitive data**
```rust
// NEVER log PII or credentials
error!(password = %password, "auth_failed");  // NO!

// Log only non-sensitive identifiers
error!(user_id = %user_id, "auth_failed");  // Yes
```

**Mistake 3: Over-logging in hot paths**
```rust
// This will murder your performance
for item in items {
    debug!(item_id = %item.id, "processing_item");  // Millions of logs!
}

// Log at boundaries instead
info!(item_count = items.len(), "batch_processing_started");
```

---

## 4. Healthy Balance Between Readable Code and Optimization

Readable code has room for optimization, but an over-optimized code at the expense of readability makes it tougher to debug.

This is probably the most valuable point in this list. Many engineering practice guides stress this:
- [Premature Optimization](https://wiki.c2.com/?PrematureOptimization)
- [Google eng-practices What to look for in a code review](https://google.github.io/eng-practices/review/reviewer/looking-for.html#complexity)
Readable code has fewer bugs, enables faster onboarding, and easier debugging.

Default to readable and maintainable code, and optimize only when profiling shows a real bottleneck. Even then, preserve clarity where possible. Premature micro-optimizations often introduce subtle bugs and make future changes and debugging much slower.

### The 90/10 Rule

There's a principle in performance optimization: [90% of execution time is spent in 10% of the code](https://softwareengineering.stackexchange.com/questions/334528/what-is-the-meaning-of-the-90-10-rule-of-program-optimization). This means most of your codebase isn't performance-critical. Write those parts for clarity and maintainability. Only when profiling identifies that critical 10% should you consider trading readability for speed.

I am not saying readability always wins, it has to be a pragmatic balance based on the domain. Bit-twiddling hacks or clever one-liners that save 2 cycles but cost hours in maintenance are a classic example of falling into the performance trap. But if profiling shows a tight loop consuming 40% of your runtime, that's the 10% where optimization makes sense.

### The Reality of Performance Bottlenecks

In most applications, performance issues come from:
- Inefficient database queries (N+1 problems, missing indexes)
- Network round-trips and API chattiness
- Poorly chosen algorithms (O(n²) where O(n log n) would work)
- Memory allocations in tight loops

Rarely are they caused by:
- Using a slightly slower but more readable language construct
- A few extra function calls
- Clear variable names vs. abbreviated ones


### When Optimization Is Appropriate

Optimize when you have profiling data showing a code segment is hot (>5% of runtime). Follow it by benchmarks to verify your optimization actually helps.
A good guideline is to add comments explaining why the optimized code looks the way it does. Refer [Rules of Optimization](https://wiki.c2.com/?RulesOfOptimization).

---

## 5. Avoid Magic Numbers and Strings

**The Principle:**  
Literal values scattered throughout code are hard to understand and dangerous to change. Extract them into named constants that explain their meaning and provide a single source of truth.

### Why This Matters

Magic numbers are mysterious values that appear in code with no explanation. When you see `if (retries > 3)` or `sleep(5000)`, you're left guessing: Why 3? Why 5000? What happens if I change it?

The problems multiply:
- **No context**: Future maintainers don't know if the value is arbitrary, carefully tuned, or mandated by a spec.
- **Scattered duplicates**: The same value appears in multiple places, making changes risky.
- **Hard to test**: How do you write tests when timeouts and thresholds are hardcoded?

### Examples: Before and After

**Bad: Magic numbers everywhere**
```rust
fn process_batch(items: &[Item]) -> Result<(), Error> {
    let cache_ttl = 3600;  // What unit? Why this value?
    let buffer_size = 8192;  // Why 8192 specifically?
    
    for attempt in 0..3 {
        match http_client.get(url).timeout(Duration::from_secs(5)).send() {
            Ok(resp) if resp.status() == StatusCode::OK => {
                // Store in cache for 3600... seconds? minutes?
                cache.set(key, value, cache_ttl)?;
                return Ok(());
            }
            Ok(resp) if resp.status() == StatusCode::TOO_MANY_REQUESTS => {
                thread::sleep(Duration::from_secs(2));
                continue;
            }
            _ => continue,
        }
    }
    Err(Error::MaxRetriesExceeded)
}
```

What are these numbers?
- `3600`: Cache TTL - seconds? minutes? hours?
- `8192`: Buffer size - why this specific value? Is it a page size? Network MTU?
- `3`: Max retry attempts
- `5`: Request timeout in seconds
- `2`: Backoff delay between retries

**Good: Named constants**
```rust
const CACHE_TTL_SECONDS: u64 = 3600;  // 1 hour cache duration
const BUFFER_SIZE_BYTES: usize = 8192;  // Standard 8KB page size
const MAX_RETRY_ATTEMPTS: u32 = 3;
const REQUEST_TIMEOUT_SECS: u64 = 5;
const RATE_LIMIT_BACKOFF_SECS: u64 = 2;

fn process_batch(items: &[Item]) -> Result<(), Error> {
    for attempt in 0..MAX_RETRY_ATTEMPTS {
        let timeout = Duration::from_secs(REQUEST_TIMEOUT_SECS);
        match http_client.get(url).timeout(timeout).send() {
            Ok(resp) if resp.status() == StatusCode::OK => {
                cache.set(key, value, CACHE_TTL_SECONDS)?;
                return Ok(());
            }
            Ok(resp) if resp.status() == StatusCode::TOO_MANY_REQUESTS => {
                thread::sleep(Duration::from_secs(RATE_LIMIT_BACKOFF_SECS));
                continue;
            }
            _ => continue,
        }
    }
    Err(Error::MaxRetriesExceeded)
}
```

Now the intent is clear. If requirements change ("increase timeout to 10 seconds"), you change one line, not hunt through the codebase.

### Configuration vs. Constants

Sometimes values should be configurable, not hardcoded:

```rust
// Application-wide settings that might vary by environment
struct Config {
    max_retry_attempts: u32,
    request_timeout_secs: u64,
    rate_limit_backoff_secs: u64,
}

// Load from environment variables or config files
impl Config {
    fn from_env() -> Self {
        Config {
            max_retry_attempts: env::var("MAX_RETRY_ATTEMPTS")
                .unwrap_or_else(|_| "3".to_string())
                .parse()
                .unwrap(),
            request_timeout_secs: env::var("REQUEST_TIMEOUT_SECS")
                .unwrap_or_else(|_| "5".to_string())
                .parse()
                .unwrap(),
            rate_limit_backoff_secs: env::var("RATE_LIMIT_BACKOFF_SECS")
                .unwrap_or_else(|_| "2".to_string())
                .parse()
                .unwrap(),
        }
    }
}
```

This lets you tune behavior without recompiling and critical for A/B tests or emergency adjustments in production.

### When Inline Values Are Acceptable

Not every number needs a constant. These are fine:

**Mathematical operations:**
```rust
let area = width * height;  // No need for RECTANGLE_SIDES = 2
let midpoint = (start + end) / 2;  // Division by 2 is self-explanatory
```

**Array indexing and iteration:**
```rust
let first = items[0];  // Obviously the first element
for i in 0..items.len() { }  // Standard iteration pattern
```

**Well-known constants:**
```rust
let circumference = 2.0 * PI * radius;  // PI is universal (use std::f64::consts::PI)
let percentage = (value / total) * 100.0;  // 100 for percentage is obvious
```

**Type conversions:**
```rust
let kilobytes = bytes / 1024;  // 1024 is the definition of KB
let timeout_ms = timeout_secs * 1000;  // 1000 ms per second is clear
```

The test is: **Would a reasonable developer understand this value without context?** If yes, inline is fine. If you need to explain it in a comment, make it a constant.

### String Literals: The Same Problem

Magic strings are just as bad:

**Bad:**
```rust
if user.role == "admin" || user.role == "superuser" {
    // What if there's a typo? "super user" vs "superuser"?
}
```

**Good:**
```rust
const ROLE_ADMIN: &str = "admin";
const ROLE_SUPERUSER: &str = "superuser";

if user.role == ROLE_ADMIN || user.role == ROLE_SUPERUSER {
    // Typos caught at compile time
}
```

**Even better: Use enums**
```rust
#[derive(Debug, PartialEq)]
enum Role {
    Admin,
    Superuser,
    Regular,
}

if user.role == Role::Admin || user.role == Role::Superuser {
    // Type-safe and exhaustively checkable
}
```

### The Testing Advantage

Named constants make tests more maintainable:

**Without constants:**
```rust
#[test]
fn test_retry_logic() {
    // How many times should this retry? 3? Why 3?
    assert_eq!(retry_request(url).attempts, 3);
}
```

**With constants:**
```rust
#[test]
fn test_retry_logic() {
    assert_eq!(retry_request(url).attempts, MAX_RETRY_ATTEMPTS);
    // Change the constant once, all tests stay in sync
}
```

---

## 6. Comments Should Explain "Why", Not "What"

**The Principle:**  
Good code is self-documenting for the "what." Comments should capture the reasoning, trade-offs, and context that isn't obvious from the code itself.

### Why This Matters

I've wasted too much time reading comments that just restate what the code does. When a comment says `// increment counter` above `counter += 1`, it adds no value.
Worse, when the code changes but the comment doesn't, you get misleading documentation.

The real question when you're debugging or modifying code isn't "what does this do?" (you can read that), it's "why does it do it this way?" That's what comments should answer.

### Bad Comments: Stating the Obvious

**Redundant comments:**
```rust
// Check if user is admin
if user.role == Role::Admin {
    // Grant admin access
    grant_access(user);
}

// Loop through all items
for item in items {
    // Process each item
    process(item);
}
```

These comments are noise. The code already says what it does.

**Worse: Comments that lie:**
```rust
// Validate email format
fn validate_input(input: &str) -> bool {
    // Code was changed to also validate phone numbers, comment wasn't updated
    input.contains('@') || input.len() == 10
}
```

When code evolves but comments don't, they become misleading. The solution isn't "update comments more carefully", it's to write fewer comments that state obvious things.

### Good Comments: Explaining Context

**Why a particular approach was chosen:**
```rust
// Use binary search instead of HashMap lookup because our profiling 
// showed that for small datasets (<100 items), the allocation overhead 
// of HashMap.insert() dominates. Binary search on a sorted Vec is 3x faster
// for our typical case of 20-50 items. See benchmark results: PERF-1234
fn find_user(users: &[User], id: UserId) -> Option<&User> {
    users.binary_search_by_key(&id, |u| u.id).ok().map(|idx| &users[idx])
}
```

**Why you're not doing something that seems obvious:**
```rust
// Don't use async/await here. This function is called from a signal handler
// where async runtime isn't available. We need blocking I/O despite the 
// performance hit. Attempted async in PR #567, caused deadlocks.
fn emergency_flush_logs() {
    std::io::stdout().flush().unwrap();
}
```

**Explaining non-obvious business logic:**
```rust
// Discount is applied AFTER tax in California due to state law AB-123.
// Other states apply discount before tax. Don't "fix" this.
let final_price = if user.state == State::California {
    (base_price * tax_rate) - discount
} else {
    (base_price - discount) * tax_rate
};
```

**Documenting external constraints:**
```rust
// The payment gateway API has an undocumented 30-second timeout.
// We set our timeout to 25s to fail fast and retry, rather than
// waiting for their server to time out. Confirmed with support ticket #8472.
const PAYMENT_TIMEOUT_SECS: u64 = 25;
```

### When Code Should Be Self-Documenting

Instead of commenting "what," write clearer code:

**Bad: Comment explains unclear code**
```rust
// Check if user has permissions
if u.r & 0x04 != 0 {
```

**Good: Code explains itself**
```rust
const PERMISSION_WRITE: u8 = 0x04;

if user.role & PERMISSION_WRITE != 0 {
```

**Even better: Use named methods**
```rust
if user.has_write_permission() {
```

### TODOs and FIXMEs: A Special Case

These are valuable comments, but they need context:

**Bad TODO:**
```rust
// TODO: fix this
fn process_data(data: &[u8]) -> Result<Output> {
    // What needs fixing? Why? When?
}
```

**Good TODO:**
```rust
// TODO(alice): Replace this O(n²) algorithm with a hash-based approach.
// Current implementation is fine for <1000 items but will be too slow
// once we support bulk imports (planned for Q2). Ticket: PERF-456
fn process_data(data: &[u8]) -> Result<Output> {
```

A good TODO includes:
- **Who**: Owner or team
- **What**: The specific issue
- **Why**: Why it's not fixed now
- **When**: Priority or timeline
- **Link**: To a ticket or design doc

### The API Documentation Exception

Public APIs need comprehensive documentation regardless of how clear the code is:

```rust
/// Calculates the haversine distance between two points on Earth.
///
/// # Arguments
/// * `lat1`, `lon1` - Coordinates of the first point in decimal degrees
/// * `lat2`, `lon2` - Coordinates of the second point in decimal degrees
///
/// # Returns
/// Distance in kilometers
///
/// # Examples
/// ```
/// let distance = haversine_distance(40.7128, -74.0060, 51.5074, -0.1278);
/// assert!((distance - 5570.0).abs() < 1.0);
/// ```
pub fn haversine_distance(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    // Implementation details...
}
```

This is different from explaining "why." API docs explain "what," "how to use," and "what to expect" for consumers who can't read the implementation.

### The Uncommented Code Smell

If you find yourself writing a long comment to explain what a section of code does, that's often a sign the code should be refactored into a well-named function:

**Before:**
```rust
// Calculate the prorated refund amount based on days remaining
// in the subscription period, accounting for the grace period
// and any outstanding credits
let refund = if days_remaining > grace_period {
    let daily_rate = subscription_cost / billing_period_days;
    let prorated = daily_rate * (days_remaining - grace_period);
    prorated - outstanding_credits
} else {
    0.0
};
```

**After:**
```rust
let refund = calculate_prorated_refund(
    subscription_cost,
    billing_period_days,
    days_remaining,
    grace_period,
    outstanding_credits
);

// Function name explains what, implementation is clear
fn calculate_prorated_refund(
    subscription_cost: f64,
    billing_period_days: u32,
    days_remaining: u32,
    grace_period: u32,
    outstanding_credits: f64,
) -> f64 {
    if days_remaining <= grace_period {
        return 0.0;
    }
    
    let daily_rate = subscription_cost / billing_period_days as f64;
    let prorated = daily_rate * (days_remaining - grace_period) as f64;
    prorated - outstanding_credits
}
```


## 7. Keep Changes Small and Focused

With vibe coding and the ease of churning out code, we might not realize as changes keep piling up, and we end up with a mammoth PR up for review. This is insanely hard for the reviewer.

I've observed that most of the time, smaller PRs get higher quality reviews, as reviewers can actually read deeply and maintain context. Larger PRs tend to sit longer in the review queue or get skimmed instead of being reviewed deeply. This could hide bugs that come to bite us in production.

Smaller PRs are:
- **Easier to understand**: Reviewers can grasp the full context without cognitive overload
- **Enable faster cycles**: Less context switching for reviewers, quicker approvals
- **Easier to revert/rollback**: If something breaks, bugs are easier to isolate. You can cherry-pick or revert a single focused change without undoing unrelated work

Some references 
- [SmartBear Study](https://smartbear.com/learn/code-review/best-practices-for-peer-code-review/): Developers can't effectively review more than 400 lines per hour
- [Google eng practices - Small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html)

### What "Small" really means in practice

- **Single logical change**: One feature, one bug fix, one refactor
- **~200-400 lines** of actual code changes (excluding generated files, tests)
- **Reviewable in one sitting**: If it takes an hour, it's too big

### The Exception: When Large PRs Are Unavoidable

Sometimes you genuinely can't break things down:
- Generated code (protobuf, OpenAPI)
- Large data migrations
- Major dependency upgrades

In these cases:
- **Warn reviewers in advance**
- **Provide a high-level summary** of what changed
- **Consider pair programming** instead of async review

---

## Closing Thoughts

These guidelines come from patterns I've observed in code reviews, feedback from seniors, and production incidents that made me think "we could have caught this earlier."

Code is read far more often than it's written. Every choice during a review should consider the developer debugging this at 3 AM, the new team member learning the codebase, and the future version of yourself who won't remember the context.

To reiterate, these aren't rigid rules. They're heuristics that tend to lead to better outcomes. When you break one of these guidelines, just make sure you're doing it consciously, with a good reason, and ideally with a comment explaining why.

