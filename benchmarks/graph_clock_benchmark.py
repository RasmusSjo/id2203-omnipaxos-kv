"""
Graph Clock Quality Benchmark Results
Usage: python graph_clock_benchmark.py
Reads from benchmark log directories with category/run-* layout
Produces:
  - Consensus latency comparison (avg + percentiles)
  - Throughput comparison
  - Fast path ratio comparison
"""
import json
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
DEFAULT_COLORS = ["#2ecc71", "#f39c12", "#e74c3c", "#3498db", "#e67e22", "#16a085"]

import sys
LOG_BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../build_scripts/logs/clock-benchmark")


def get_categories() -> list[str]:
    categories = [path.name for path in LOG_BASE.iterdir() if path.is_dir()] if LOG_BASE.exists() else []
    preferred = [quality for quality in QUALITIES if quality in categories]
    remaining = sorted(category for category in categories if category not in QUALITIES)
    return preferred + remaining


def category_label(category: str) -> str:
    return QUALITY_LABELS.get(category, category.replace("_", "\n"))


def category_color(category: str, index: int) -> str:
    return QUALITY_COLORS.get(category, DEFAULT_COLORS[index % len(DEFAULT_COLORS)])


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
                    df["run_id"] = run_dir.name
                    df["client_id"] = csv_file.stem
                    dfs.append(df)
            except Exception as e:
                print(f"WARNING: Could not load {csv_file}: {e}")
    return dfs


def load_fast_path_stats(category: str) -> list[dict]:
    stats = []
    category_dir = LOG_BASE / category
    if not category_dir.exists():
        print(f"WARNING: No data found for category '{category}' at {category_dir}")
        return stats

    for run_dir in sorted(category_dir.glob("run-*")):
        leader_stat = None
        for server_file in sorted(run_dir.glob("server-*.json")):
            try:
                server_data = json.loads(server_file.read_text())
            except (ValueError, OSError) as exc:
                print(f"WARNING: Could not load {server_file}: {exc}")
                continue

            server_stat = {
                "run_id": run_dir.name,
                "fast_path_ratio": float(server_data["fast_path_ratio"]),
                "fast_path_decisions": int(server_data["fast_path_decisions"]),
                "slow_path_decisions": int(server_data["slow_path_decisions"]),
                "server_id": int(server_data["config"]["server_id"]),
            }
            if leader_stat is None:
                leader_stat = server_stat
                continue

            current_total = (
                leader_stat["fast_path_decisions"] + leader_stat["slow_path_decisions"]
            )
            candidate_total = (
                server_stat["fast_path_decisions"] + server_stat["slow_path_decisions"]
            )
            if candidate_total > current_total:
                leader_stat = server_stat

        if leader_stat is not None:
            stats.append(leader_stat)
    return stats


def compute_latency_stats(dfs: list[pd.DataFrame]) -> dict:
    """Compute latency statistics from run-level aggregates across all runs."""
    if not dfs:
        return {}

    combined = pd.concat(dfs, ignore_index=True)
    run_stats = []
    for _, run_df in combined.groupby("run_id"):
        latencies = run_df["response_latency"]
        if latencies.empty:
            continue
        run_stats.append(
            {
                "mean": latencies.mean(),
                "median": latencies.median(),
                "p95": latencies.quantile(0.95),
                "p99": latencies.quantile(0.99),
            }
        )

    if not run_stats:
        return {}

    run_stats_df = pd.DataFrame(run_stats)
    return {
        "mean":   run_stats_df["mean"].mean(),
        "mean_std": run_stats_df["mean"].std(ddof=0),
        "median": run_stats_df["median"].mean(),
        "p95":    run_stats_df["p95"].mean(),
        "p95_std": run_stats_df["p95"].std(ddof=0),
        "p99":    run_stats_df["p99"].mean(),
        "p99_std": run_stats_df["p99"].std(ddof=0),
    }


def compute_throughput(dfs: list[pd.DataFrame]) -> dict:
    """Compute run-level throughput as completed responses divided by run duration."""
    if not dfs:
        return {}

    combined = pd.concat(dfs, ignore_index=True)
    throughputs = []
    for _, run_df in combined.groupby("run_id"):
        start_ms = run_df["request_time"].min()
        end_ms = run_df["response_time"].max()
        duration_sec = (end_ms - start_ms) / 1000
        if duration_sec <= 0:
            continue
        throughputs.append(len(run_df) / duration_sec)

    if not throughputs:
        return {}

    return {
        "mean": np.mean(throughputs),
        "std":  np.std(throughputs),
    }


def compute_fast_path_ratio_stats(run_stats: list[dict]) -> dict:
    """Compute run-level fast path ratio stats from the leader server of each run."""
    if not run_stats:
        return {}

    ratios = [stat["fast_path_ratio"] for stat in run_stats]
    return {
        "mean": np.mean(ratios),
        "std": np.std(ratios),
    }


def plot_latency_comparison(stats: dict, categories: list[str]):
    """Bar chart comparing run-level latency aggregates with error bars."""
    fig, ax = plt.subplots(figsize=(10, 6))

    categories = [c for c in categories if c in stats and stats[c]]
    x = np.arange(len(categories))
    width = 0.25

    means  = [stats[c]["mean"]   for c in categories]
    p95s   = [stats[c]["p95"]    for c in categories]
    p99s   = [stats[c]["p99"]    for c in categories]
    mean_stds = [stats[c]["mean_std"] for c in categories]
    p95_stds  = [stats[c]["p95_std"]  for c in categories]
    p99_stds  = [stats[c]["p99_std"]  for c in categories]
    colors = [category_color(c, i) for i, c in enumerate(categories)]
    labels = [category_label(c) for c in categories]

    bars_mean = ax.bar(
        x - width, means, width, label="Mean", color=colors, alpha=0.9,
        yerr=mean_stds, capsize=6, error_kw={"elinewidth": 2}
    )
    bars_p95 = ax.bar(
        x, p95s, width, label="P95", color=colors, alpha=0.6,
        yerr=p95_stds, capsize=6, error_kw={"elinewidth": 2}
    )
    bars_p99 = ax.bar(
        x + width, p99s, width, label="P99", color=colors, alpha=0.35,
        yerr=p99_stds, capsize=6, error_kw={"elinewidth": 2}
    )

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

    ax.set_xlabel("Benchmark Category", fontsize=13)
    ax.set_ylabel("Consensus Latency (ms)", fontsize=13)
    ax.set_title("Consensus Latency by Category", fontsize=15)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.legend(fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    return fig


def plot_throughput_comparison(throughput_stats: dict, categories: list[str]):
    """Bar chart comparing throughput."""
    fig, ax = plt.subplots(figsize=(8, 5))

    categories = [c for c in categories if c in throughput_stats and throughput_stats[c]]
    x = np.arange(len(categories))
    means  = [throughput_stats[c]["mean"] for c in categories]
    stds   = [throughput_stats[c]["std"]  for c in categories]
    colors = [category_color(c, i) for i, c in enumerate(categories)]
    labels = [category_label(c) for c in categories]

    bars = ax.bar(x, means, color=colors, alpha=0.85,
                  yerr=stds, capsize=6, error_kw={"elinewidth": 2})

    for bar, mean in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(stds) * 0.05,
                f"{mean:.1f}", ha="center", va="bottom", fontsize=11)

    ax.set_xlabel("Benchmark Category", fontsize=13)
    ax.set_ylabel("Throughput (responses/sec)", fontsize=13)
    ax.set_title("Throughput by Category", fontsize=15)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    return fig


def plot_latency_over_time(all_dfs: dict, categories: list[str]):
    """Line chart showing latency over experiment time for each quality."""
    fig, ax = plt.subplots(figsize=(12, 5))
    epoch_start = pd.Timestamp("20180606")

    for i, category in enumerate(categories):
        dfs = all_dfs.get(category, [])
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
                label=category_label(category).replace("\n", " "),
                color=category_color(category, i), linewidth=2)

    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%M:%S"))
    ax.set_xlabel("Experiment Time", fontsize=13)
    ax.set_ylabel("Avg Consensus Latency (ms)", fontsize=13)
    ax.set_title("Consensus Latency Over Time by Category", fontsize=15)
    ax.legend(fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    return fig


def plot_fast_path_ratio_comparison(fast_path_stats: dict, categories: list[str]):
    """Bar chart comparing run-level fast path ratio."""
    fig, ax = plt.subplots(figsize=(8, 5))

    categories = [c for c in categories if c in fast_path_stats and fast_path_stats[c]]
    x = np.arange(len(categories))
    means = [fast_path_stats[c]["mean"] for c in categories]
    stds = [fast_path_stats[c]["std"] for c in categories]
    colors = [category_color(c, i) for i, c in enumerate(categories)]
    labels = [category_label(c) for c in categories]

    bars = ax.bar(
        x, means, color=colors, alpha=0.85,
        yerr=stds, capsize=6, error_kw={"elinewidth": 2}
    )

    for bar, mean in zip(bars, means):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.02,
            f"{mean:.2f}",
            ha="center",
            va="bottom",
            fontsize=11,
        )

    ax.set_xlabel("Benchmark Category", fontsize=13)
    ax.set_ylabel("Fast Path Ratio", fontsize=13)
    ax.set_title("Fast Path Ratio by Category", fontsize=15)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0, top=1.05)
    ax.legend(fontsize=11)
    plt.tight_layout()
    return fig


def save_figures(figures: list[tuple[str, plt.Figure]]):
    output_dir = LOG_BASE / "plots"
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, fig in figures:
        fig.savefig(output_dir / filename, dpi=200, bbox_inches="tight")


def print_summary(stats: dict, throughput_stats: dict, fast_path_stats: dict, categories: list[str]):
    print("\n" + "="*60)
    print("BENCHMARK SUMMARY")
    print("="*60)
    for category in categories:
        label = category_label(category).replace("\n", " ")
        print(f"\n{label}")
        if category in stats and stats[category]:
            s = stats[category]
            print(f"  Latency  — mean: {s['mean']:.2f}ms  median: {s['median']:.2f}ms  "
                  f"p95: {s['p95']:.2f}ms  p99: {s['p99']:.2f}ms")
        else:
            print("  Latency  — no data")
        if category in throughput_stats and throughput_stats[category]:
            t = throughput_stats[category]
            print(f"  Throughput — mean: {t['mean']:.1f} req/s  std: {t['std']:.1f}")
        else:
            print("  Throughput — no data")
        if category in fast_path_stats and fast_path_stats[category]:
            f = fast_path_stats[category]
            print(f"  Fast path — mean ratio: {f['mean']:.3f}  std: {f['std']:.3f}")
        else:
            print("  Fast path — no data")
    print("="*60)


def main():
    categories = get_categories()
    all_dfs = {}
    latency_stats = {}
    throughput_stats = {}
    fast_path_stats = {}

    for category in categories:
        dfs = load_client_csvs(category)
        all_dfs[category] = dfs
        latency_stats[category] = compute_latency_stats(dfs)
        throughput_stats[category] = compute_throughput(dfs)
        fast_path_stats[category] = compute_fast_path_ratio_stats(load_fast_path_stats(category))

    print_summary(latency_stats, throughput_stats, fast_path_stats, categories)

    fig1 = plot_latency_comparison(latency_stats, categories)
    fig2 = plot_throughput_comparison(throughput_stats, categories)
    fig3 = plot_fast_path_ratio_comparison(fast_path_stats, categories)
    fig4 = plot_latency_over_time(all_dfs, categories)
    save_figures([
        ("latency_comparison.png", fig1),
        ("throughput_comparison.png", fig2),
        ("fast_path_ratio_comparison.png", fig3),
        ("latency_over_time.png", fig4),
    ])

    plt.show()
    plt.close("all")


if __name__ == "__main__":
    main()
