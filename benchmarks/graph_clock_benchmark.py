"""
Graph Clock Quality Benchmark Results
Usage: python graph_clock_benchmark.py
Reads from logs/clock-benchmark/{high,medium,low}/run-*/
Produces:
  - Consensus latency comparison (avg + percentiles)
  - Throughput comparison
"""
import json
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

QUALITIES = ["high", "medium", "low"]
QUALITY_LABELS = {
    "high":   "High Quality\n(±10μs, 1ms sync)",
    "medium": "Medium Quality\n(±100μs, 10ms sync)",
    "low":    "Low Quality\n(±1ms, 100ms sync)",
}
QUALITY_COLORS = {
    "high":   "#2ecc71",
    "medium": "#f39c12",
    "low":    "#e74c3c",
}

import sys
LOG_BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../build_scripts/logs/clock-benchmark")


def load_client_csvs(quality: str) -> list[pd.DataFrame]:
    """Load all client CSVs for a quality level across all runs."""
    dfs = []
    quality_dir = LOG_BASE / quality
    if not quality_dir.exists():
        print(f"WARNING: No data found for quality '{quality}' at {quality_dir}")
        return dfs
    for run_dir in sorted(quality_dir.glob("run-*")):
        for csv_file in run_dir.glob("client-*.csv"):
            try:
                df = pd.read_csv(
                    csv_file,
                    names=["request_time", "write", "response_time"],
                    header=0,
                    dtype={"write": "bool"},
                )
                df["response_latency"] = df["response_time"] - df["request_time"]
                df = df.dropna(subset=["response_latency"])
                df = df[df["response_latency"] > 0]
                if not df.empty:
                    dfs.append(df)
            except Exception as e:
                print(f"WARNING: Could not load {csv_file}: {e}")
    return dfs


def compute_latency_stats(dfs: list[pd.DataFrame]) -> dict:
    """Compute latency statistics across all runs."""
    if not dfs:
        return {}
    all_latencies = pd.concat([df["response_latency"] for df in dfs])
    return {
        "mean":   all_latencies.mean(),
        "median": all_latencies.median(),
        "p95":    all_latencies.quantile(0.95),
        "p99":    all_latencies.quantile(0.99),
        "std":    all_latencies.std(),
    }


def compute_throughput(dfs: list[pd.DataFrame]) -> dict:
    """Compute average throughput (responses/sec) across all runs."""
    if not dfs:
        return {}
    throughputs = []
    for df in dfs:
        df = df.copy()
        df["request_time"] = pd.to_datetime(df["request_time"], unit="ms")
        df = df.set_index("request_time")
        rps = df["response_latency"].resample("1s").count()
        rps = rps[rps > 0]
        if not rps.empty:
            throughputs.append(rps.mean())
    if not throughputs:
        return {}
    return {
        "mean": np.mean(throughputs),
        "std":  np.std(throughputs),
    }


def plot_latency_comparison(stats: dict):
    """Bar chart comparing mean latency with p95/p99 error bars."""
    fig, ax = plt.subplots(figsize=(10, 6))

    qualities = [q for q in QUALITIES if q in stats and stats[q]]
    x = np.arange(len(qualities))
    width = 0.25

    means  = [stats[q]["mean"]   for q in qualities]
    p95s   = [stats[q]["p95"]    for q in qualities]
    p99s   = [stats[q]["p99"]    for q in qualities]
    stds   = [stats[q]["std"]    for q in qualities]
    colors = [QUALITY_COLORS[q]  for q in qualities]
    labels = [QUALITY_LABELS[q]  for q in qualities]

    bars_mean = ax.bar(x - width, means,  width, label="Mean",   color=colors, alpha=0.9)
    bars_p95  = ax.bar(x,         p95s,   width, label="P95",    color=colors, alpha=0.6)
    bars_p99  = ax.bar(x + width, p99s,   width, label="P99",    color=colors, alpha=0.35)

    # Add value labels on bars
    for bar in bars_mean:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                f"{bar.get_height():.1f}", ha="center", va="bottom", fontsize=9)
    for bar in bars_p95:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                f"{bar.get_height():.1f}", ha="center", va="bottom", fontsize=9)
    for bar in bars_p99:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                f"{bar.get_height():.1f}", ha="center", va="bottom", fontsize=9)

    ax.set_xlabel("Clock Quality", fontsize=13)
    ax.set_ylabel("Consensus Latency (ms)", fontsize=13)
    ax.set_title("Consensus Latency vs Clock Quality", fontsize=15)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.legend(fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    return fig


def plot_throughput_comparison(throughput_stats: dict):
    """Bar chart comparing throughput."""
    fig, ax = plt.subplots(figsize=(8, 5))

    qualities = [q for q in QUALITIES if q in throughput_stats and throughput_stats[q]]
    x = np.arange(len(qualities))
    means  = [throughput_stats[q]["mean"] for q in qualities]
    stds   = [throughput_stats[q]["std"]  for q in qualities]
    colors = [QUALITY_COLORS[q]           for q in qualities]
    labels = [QUALITY_LABELS[q]           for q in qualities]

    bars = ax.bar(x, means, color=colors, alpha=0.85,
                  yerr=stds, capsize=6, error_kw={"elinewidth": 2})

    for bar, mean in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(stds) * 0.05,
                f"{mean:.1f}", ha="center", va="bottom", fontsize=11)

    ax.set_xlabel("Clock Quality", fontsize=13)
    ax.set_ylabel("Throughput (responses/sec)", fontsize=13)
    ax.set_title("Throughput vs Clock Quality", fontsize=15)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    return fig


def plot_latency_over_time(all_dfs: dict):
    """Line chart showing latency over experiment time for each quality."""
    fig, ax = plt.subplots(figsize=(12, 5))
    epoch_start = pd.Timestamp("20180606")

    for quality in QUALITIES:
        dfs = all_dfs.get(quality, [])
        if not dfs:
            continue
        combined = pd.concat(dfs)
        combined = combined.copy()
        combined["request_time"] = pd.to_datetime(combined["request_time"], unit="ms")
        combined = combined.set_index("request_time").sort_index()
        start = combined.index.min()
        combined.index = epoch_start + (combined.index - start)
        avg_latency = combined["response_latency"].resample("1s").mean()
        ax.plot(avg_latency.index, avg_latency.values,
                label=QUALITY_LABELS[quality].replace("\n", " "),
                color=QUALITY_COLORS[quality], linewidth=2)

    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%M:%S"))
    ax.set_xlabel("Experiment Time", fontsize=13)
    ax.set_ylabel("Avg Consensus Latency (ms)", fontsize=13)
    ax.set_title("Consensus Latency Over Time by Clock Quality", fontsize=15)
    ax.legend(fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    return fig


def print_summary(stats: dict, throughput_stats: dict):
    print("\n" + "="*60)
    print("CLOCK QUALITY BENCHMARK SUMMARY")
    print("="*60)
    for q in QUALITIES:
        label = QUALITY_LABELS[q].replace("\n", " ")
        print(f"\n{label}")
        if q in stats and stats[q]:
            s = stats[q]
            print(f"  Latency  — mean: {s['mean']:.2f}ms  median: {s['median']:.2f}ms  "
                  f"p95: {s['p95']:.2f}ms  p99: {s['p99']:.2f}ms")
        else:
            print("  Latency  — no data")
        if q in throughput_stats and throughput_stats[q]:
            t = throughput_stats[q]
            print(f"  Throughput — mean: {t['mean']:.1f} req/s  std: {t['std']:.1f}")
        else:
            print("  Throughput — no data")
    print("="*60)


def main():
    all_dfs = {}
    latency_stats = {}
    throughput_stats = {}

    for quality in QUALITIES:
        dfs = load_client_csvs(quality)
        all_dfs[quality] = dfs
        latency_stats[quality] = compute_latency_stats(dfs)
        throughput_stats[quality] = compute_throughput(dfs)

    print_summary(latency_stats, throughput_stats)

    fig1 = plot_latency_comparison(latency_stats)
    fig2 = plot_throughput_comparison(throughput_stats)
    fig3 = plot_latency_over_time(all_dfs)

    plt.show()
    plt.close("all")


if __name__ == "__main__":
    main()
