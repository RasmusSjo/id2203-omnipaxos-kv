#!/bin/bash
# Clock Quality Benchmark
# Runs 3 clock quality configurations (high/medium/low) each NUM_RUNS times.
# Results saved to ./logs/clock-benchmark/{high,medium,low}/run-N/
#
# Usage: ./run-clock-benchmark.sh [num_runs]
# Example: ./run-clock-benchmark.sh 3

NUM_RUNS=${1:-3}
CLUSTER_SIZE=3
RUST_LOG="info"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_CONFIG="$SCRIPT_DIR/cluster-config.toml"
LOG_BASE="$SCRIPT_DIR/logs/clock-benchmark"

QUALITIES=("high" "medium" "low")

# Kill all child processes on Ctrl+C
interrupt() {
    echo "Interrupted, killing all servers..."
    pkill -P $$
    exit 1
}
trap "interrupt" SIGINT

run_once() {
    local quality=$1
    local run=$2
    local log_dir="$LOG_BASE/$quality/run-$run"

    echo ""
    echo "========================================="
    echo "RUNNING: clock-benchmark/$quality/run-$run"
    echo "========================================="

    mkdir -p "$log_dir"

    # Write client config with correct output paths
    local client_config="$log_dir/client-1-config.toml"
    cat > "$client_config" << TOML
location = "local-1"
server_id = 1
server_address = "127.0.0.1:8001"
summary_filepath = "$log_dir/client-1.json"
output_filepath = "$log_dir/client-1.csv"

[[requests]]
duration_sec = 20
requests_per_sec = 10000
read_ratio = 0.5
TOML

    # Write server configs with correct output paths
    for i in 1 2 3 4 5; do
        local src="$SCRIPT_DIR/$quality/server-$i-config.toml"
        local dst="$log_dir/server-$i-config.toml"
        sed "s|output_filepath = .*|output_filepath = \"$log_dir/server-$i.json\"|" "$src" > "$dst"
    done

    # Start servers
    local server_pids=()
    for i in 1 2 3 4 5; do
        RUST_LOG=$RUST_LOG \
        SERVER_CONFIG_FILE="$log_dir/server-$i-config.toml" \
        CLUSTER_CONFIG_FILE="$CLUSTER_CONFIG" \
        cargo run --manifest-path="$SCRIPT_DIR/../Cargo.toml" --release --bin server \
            2> "$log_dir/server-$i-stderr.log" &
        server_pids+=($!)
    done

    # Wait for servers to start
    echo "Waiting for servers to start..."
    sleep 3

    # Run client
    RUST_LOG=$RUST_LOG \
    CONFIG_FILE="$client_config" \
    cargo run --manifest-path="$SCRIPT_DIR/../Cargo.toml" --release --bin client \
        2> "$log_dir/client-1-stderr.log"

    echo "Client finished. Waiting 5 seconds for final server stats snapshot..."
    sleep 5

    echo "Stopping servers..."

    # Kill servers
    for pid in "${server_pids[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait "${server_pids[@]}" 2>/dev/null

    echo "Results saved to $log_dir"
    sleep 1
}

echo "Starting Clock Quality Benchmark"
echo "Qualities: ${QUALITIES[*]}"
echo "Runs per quality: $NUM_RUNS"
echo ""

# Build first to avoid noise in timing
echo "Building project..."
cargo build --manifest-path="$SCRIPT_DIR/../Cargo.toml" --release --bin server --bin client 2>/dev/null
echo "Build complete."

for quality in "${QUALITIES[@]}"; do
    for ((run=0; run<NUM_RUNS; run++)); do
        run_once "$quality" "$run"
    done
done

echo ""
echo "========================================="
echo "Clock Quality Benchmark COMPLETE"
echo "Results in: $LOG_BASE"
echo "========================================="
