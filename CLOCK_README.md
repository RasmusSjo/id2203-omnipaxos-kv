# Clock Simulator (Summary)

## IMPORTANT: Currently, I divide the get_time() and get_uncertainty() into two separate methods, but they are designed to be called together. If two concurrent calls to get_time() and get_uncertainty() are made, the uncertainty may not align with the time. To address this, I added get_time_with_uncertainty() which returns both together atomically. 

## How the clock is simulated
- **True time** is derived from one of the following:
  - `SystemTime::now()` minus a shared `start_unix_ms` (if configured), or
  - `Instant::now()` relative to the process start (default).
- The true time is then scaled by `time_scale` to produce simulated microseconds.
- **Drift** is applied as `drift_rate_us_per_sec` over simulated elapsed time.
- The **simulated clock time** is:
  - `true_time + drift + offset`
- The clock is **clamped** on every `get_time()` so that:
  - `sim_time` stays within `true_time ± sync_uncertainty_us`
  - **Monotonicity** is preserved by not returning values below the last returned time　within the same sync period.
- **Resync** happens every `sync_period_us` (simulated time).
  - At resync, a new `offset` is chosen so that `sim_time` is within `true_time ± sync_uncertainty_us`.
  - Since, we assume `true_time` is within `sim_time ± sync_uncertainty_us` of the actual time, resync ensures that new `sim_time` is within `true_time ± sync_uncertainty_us`.

## How the clock is used by OmniPaxos
- The clock implements `PhysicalClock` directly.
- `get_time()` and `get_uncertainty()` are **`&self`** methods.
- `get_uncertainty()` is aligned with the **most recent `get_time()`** result.
- If you need an atomic pair, use `get_time_with_uncertainty()`.

## Per-server clock
- Each server process initializes **one clock instance**.
- The server uses a `OnceLock<Clock>` and passes `&Clock` into `OmniPaxosConfig::build(...)`.
- This means **one clock per server process** (which matches the default setup: one server per process).
- Therefore, we did not implement any clock sharing or synchronization between servers, but assume that each server's `Instant::now()` is a reasonable approximation of the same "true time" for all servers.

## Config
Clock parameters live under the `[clock]` section in the server config TOML:
```toml
[clock]
drift_rate_us_per_sec = 0
sync_uncertainty_us = 100
sync_period_us = 10000
time_scale = 1
seed = 42
start_unix_ms = 0
```
