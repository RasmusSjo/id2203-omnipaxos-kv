pub mod simulator {
    use rand::{rngs::StdRng, Rng, SeedableRng};
    use serde::{Deserialize, Serialize};
    use std::sync::{Arc, Mutex};
    use std::thread::{self, JoinHandle};
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    pub type NodeId = u64;

    #[derive(Debug, Clone, Default, Deserialize, Serialize)]
    pub struct ClockConfig {
        pub node_id: NodeId,
        /// Drift rate in microseconds per simulated second.
        pub drift_rate_us_per_sec: i64,
        /// Synchronization uncertainty bound (±ε) in microseconds.
        pub sync_uncertainty_us: i64,
        /// Synchronization period in simulated microseconds.
        pub sync_period_us: i64,
        /// Real-to-simulated time scale (1 means 1 real microsecond = 1 simulated microsecond).
        pub time_scale: i64,
        /// Optional RNG seed for deterministic simulation.
        pub seed: Option<u64>,
        /// Optional shared epoch in UNIX milliseconds for aligning true time across servers.
        pub start_unix_ms: Option<i64>,
    }

    #[derive(Debug)]
    struct ClockState {
        base_sim_offset_us: i64,
        last_sim_us: i64,
        // Next resync deadline in simulated time (true clock).
        next_resync_sim_us: i64,
        rng: StdRng,
        last_true_time: i64,
        last_sim_time: i64,
    }

    #[derive(Debug)]
    pub struct Clock {
        cfg: ClockConfig,
        base_real: Instant,
        base_true_sim_us: i64,
        state: Mutex<ClockState>,
    }

    impl Clock {
        pub fn new(cfg: ClockConfig) -> Self {
            assert!(cfg.sync_uncertainty_us >= 0, "sync_uncertainty_us must be >= 0");
            assert!(cfg.sync_period_us > 0, "sync_period_us must be > 0");
            assert!(cfg.time_scale > 0, "time_scale must be > 0");

            let base_real = Instant::now();
            let base_true_sim_us = 0_i64;
            let seed = cfg.seed.unwrap_or_else(|| {
                // Non-deterministic seed when not provided.
                rand::thread_rng().gen::<u64>()
            });
            let rng = StdRng::seed_from_u64(seed);

            let next_resync_sim_us = cfg.sync_period_us;

            Clock {
                cfg,
                base_real,
                base_true_sim_us,
                state: Mutex::new(ClockState {
                    base_sim_offset_us: 0,
                    last_sim_us: base_true_sim_us,
                    next_resync_sim_us,
                    rng,
                    last_true_time: base_true_sim_us,
                    last_sim_time: base_true_sim_us,
                }),
            }
        }

        pub fn node_id(&self) -> NodeId {
            self.cfg.node_id
        }
        
        pub fn get_time(&self) -> i64 {
            let now = Instant::now();
            let true_time = self.compute_true_time(now);
            let mut state = self.state.lock().unwrap();

            // Resync if needed.
            if true_time >= state.next_resync_sim_us {
                self.resync(now, true_time, &mut state);
            }

            let sim_time = self.compute_sim_time(now, state.base_sim_offset_us);
            let (min_allowed, max_allowed) = self.allowed_range(true_time);
            let clamped =
                self.clamp_sim_time(sim_time, min_allowed, max_allowed, &mut state);
            state.last_sim_us = clamped;
            state.last_true_time = true_time;
            state.last_sim_time = clamped;
            clamped
        }

        pub fn get_time_with_uncertainty(&self) -> (i64, i64) {
            let now = Instant::now();
            let true_time = self.compute_true_time(now);
            let mut state = self.state.lock().unwrap();

            // Resync if needed.
            if true_time >= state.next_resync_sim_us {
                self.resync(now, true_time, &mut state);
            }

            let sim_time = self.compute_sim_time(now, state.base_sim_offset_us);
            let (min_allowed, max_allowed) = self.allowed_range(true_time);
            let clamped =
                self.clamp_sim_time(sim_time, min_allowed, max_allowed, &mut state);
            state.last_sim_us = clamped;
            state.last_true_time = true_time;
            state.last_sim_time = clamped;

            let error_abs = abs_i64(state.last_sim_time - state.last_true_time);
            let upper = self.cfg.sync_uncertainty_us.max(error_abs);
            let unc = if upper == 0 || upper == error_abs {
                upper
            } else {
                state.rng.gen_range(error_abs..=upper)
            };
            (clamped, unc)
        }

        pub fn get_true_time(&self) -> i64 {
            self.compute_true_time(Instant::now())
        }

        pub fn get_last_true_time(&self) -> i64 {
            let state = self.state.lock().unwrap();
            state.last_true_time
        }

        pub fn get_uncertainty(&self) -> i64 {
            let mut state = self.state.lock().unwrap();
            let error_abs = abs_i64(state.last_sim_time - state.last_true_time);
            let upper = self.cfg.sync_uncertainty_us.max(error_abs);
            if upper == 0 || upper == error_abs {
                return upper;
            }
            state.rng.gen_range(error_abs..=upper)
        }
        
        /// Starts a background thread that resyncs at the configured interval.
        /// The thread runs indefinitely; manage its lifetime externally if needed.
        pub fn start_auto_resync(clock: Arc<Clock>) -> JoinHandle<()> {
            thread::spawn(move || loop {
                let sleep_dur = {
                    let now = Instant::now();
                    let true_time = clock.compute_true_time(now);
                    let state = clock.state.lock().unwrap();
                    let remaining_sim_us = state.next_resync_sim_us.saturating_sub(true_time);
                    sim_us_to_real_duration(remaining_sim_us, clock.cfg.time_scale)
                };
                if sleep_dur > Duration::from_micros(0) {
                    thread::sleep(sleep_dur);
                }
                let now2 = Instant::now();
                let true_time2 = clock.compute_true_time(now2);
                let mut state = clock.state.lock().unwrap();
                if true_time2 >= state.next_resync_sim_us {
                    clock.resync(now2, true_time2, &mut state);
                }
            })
        }

        fn compute_true_time(&self, now: Instant) -> i64 {
            if let Some(start_ms) = self.cfg.start_unix_ms {
                let start_us = (start_ms as i128) * 1_000_i128;
                let now_us = system_time_us();
                let elapsed_us = now_us.saturating_sub(start_us);
                let sim_elapsed_us = elapsed_us.saturating_mul(self.cfg.time_scale as i128);
                clamp_i128_to_i64(sim_elapsed_us)
            } else {
                let real_elapsed_us = duration_to_us(now.duration_since(self.base_real));
                let sim_elapsed_us = real_elapsed_us.saturating_mul(self.cfg.time_scale);
                self.base_true_sim_us.saturating_add(sim_elapsed_us)
            }
        }

        fn compute_sim_time(&self, now: Instant, base_sim_offset_us: i64) -> i64 {
            let real_elapsed_us = duration_to_us(now.duration_since(self.base_real));
            let sim_elapsed_us = real_elapsed_us.saturating_mul(self.cfg.time_scale);

            let drift_us = (sim_elapsed_us as i128 * self.cfg.drift_rate_us_per_sec as i128)
                / 1_000_000_i128;

            let sim_time = self.base_true_sim_us as i128
                + sim_elapsed_us as i128
                + base_sim_offset_us as i128
                + drift_us;

            clamp_i128_to_i64(sim_time)
        }

        fn allowed_range(&self, true_time: i64) -> (i64, i64) {
            let bound = self.cfg.sync_uncertainty_us.max(0);
            let min_allowed = true_time.saturating_sub(bound);
            let max_allowed = true_time.saturating_add(bound);
            (min_allowed, max_allowed)
        }

        fn clamp_sim_time(
            &self,
            sim_time: i64,
            min_allowed: i64,
            max_allowed: i64,
            state: &mut ClockState,
        ) -> i64 {
            let lower = std::cmp::max(state.last_sim_us, min_allowed);
            let clamped = if sim_time < lower {
                lower
            } else if sim_time > max_allowed {
                max_allowed
            } else {
                sim_time
            };
            if clamped != sim_time {
                // Adjust offset to keep future reads consistent.
                state.base_sim_offset_us =
                    state.base_sim_offset_us.saturating_add(clamped - sim_time);
            }
            clamped
        }

        fn resync(&self, now: Instant, true_time: i64, state: &mut ClockState) {
            let (min_allowed, max_allowed) = self.allowed_range(true_time);
            let sim_time = self.compute_sim_time(now, state.base_sim_offset_us);
            let sim_time_no_offset = sim_time.saturating_sub(state.base_sim_offset_us);
            // let bound = self.cfg.sync_uncertainty_us.max(0);
            // let new_error = if bound == 0 {
            //     0
            // } else {
            //     state.rng.gen_range(-bound..=bound)
            // };
            let new_error = 0; // For simplicity
            let target_time = true_time.saturating_add(new_error);
            let min_target = min_allowed;
            let max_target = max_allowed;
            let clamped_target = if target_time < min_target {
                min_target
            } else if target_time > max_target {
                max_target
            } else {
                target_time
            };
            state.base_sim_offset_us = clamped_target.saturating_sub(sim_time_no_offset);
            state.next_resync_sim_us = true_time.saturating_add(self.cfg.sync_period_us);
        }
    }

    fn duration_to_us(d: Duration) -> i64 {
        let secs = d.as_secs() as i128;
        let micros = d.subsec_micros() as i128;
        clamp_i128_to_i64(secs * 1_000_000_i128 + micros)
    }

    fn sim_us_to_real_duration(sim_us: i64, time_scale: i64) -> Duration {
        let real_us = (sim_us as i128) / (time_scale as i128);
        let real_us = if real_us < 0 { 0 } else { real_us } as u64;
        Duration::from_micros(real_us)
    }

    fn clamp_i128_to_i64(v: i128) -> i64 {
        if v > i64::MAX as i128 {
            i64::MAX
        } else if v < i64::MIN as i128 {
            i64::MIN
        } else {
            v as i64
        }
    }

    fn system_time_us() -> i128 {
        match SystemTime::now().duration_since(UNIX_EPOCH) {
            Ok(d) => d.as_micros() as i128,
            Err(_) => 0,
        }
    }

    fn abs_i64(v: i64) -> i64 {
        let v = v as i128;
        if v < 0 {
            (-v).min(i64::MAX as i128) as i64
        } else {
            v as i64
        }
    }

}

impl omnipaxos::util::PhysicalClock for simulator::Clock {
    fn get_time(&self) -> i64 {
        simulator::Clock::get_time(self)
    }

    fn get_uncertainty(&self) -> i64 {
        simulator::Clock::get_uncertainty(self)
    }

    fn get_time_with_uncertainty(&self) -> (i64, i64) {
        simulator::Clock::get_time_with_uncertainty(self)
    }
}
