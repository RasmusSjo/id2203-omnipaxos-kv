#!/usr/bin/env python3
import argparse
import csv


def main():
    parser = argparse.ArgumentParser(description="Plot clock simulation output")
    parser.add_argument("--csv", default="scripts/clock_sim.csv", help="Input CSV path")
    parser.add_argument("--out", default="scripts/clock_sim.png", help="Output PNG path")
    parser.add_argument("--show", action="store_true", help="Show plot window")
    args = parser.parse_args()

    import matplotlib.pyplot as plt

    rows = []
    with open(args.csv, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    if not rows:
        raise SystemExit("CSV is empty")

    series = {}
    has_error = "error_us" in rows[0]
    for r in rows:
        node = int(r["node_id"])
        t_real_ms = float(r["real_ms"])
        t_sim_us = float(r["sim_us"])
        unc_us = float(r["uncertainty_us"])
        series.setdefault(node, {"real_ms": [], "sim_us": [], "unc_us": [], "error_us": []})
        series[node]["real_ms"].append(t_real_ms)
        series[node]["sim_us"].append(t_sim_us)
        series[node]["unc_us"].append(unc_us)
        if has_error:
            series[node]["error_us"].append(float(r["error_us"]))

    if has_error:
        fig, ax = plt.subplots(1, 1, figsize=(10, 6), sharex=True)
    else:
        fig, ax = plt.subplots(1, 1, figsize=(10, 6), sharex=True)

    if has_error:
        for node, data in sorted(series.items()):
            (line,) = ax.plot(data["real_ms"], data["error_us"], label=f"node {node}")
            color = line.get_color()
            unc_pos = [e + u for e, u in zip(data["error_us"], data["unc_us"])]
            unc_neg = [e - u for e, u in zip(data["error_us"], data["unc_us"])]
            ax.fill_between(
                data["real_ms"],
                unc_neg,
                unc_pos,
                color=color,
                alpha=0.08,
                linewidth=0,
            )
            ax.plot(
                data["real_ms"],
                unc_pos,
                color=color,
                alpha=0.4,
                linestyle="--",
                linewidth=1,
            )
            ax.plot(
                data["real_ms"],
                unc_neg,
                color=color,
                alpha=0.4,
                linestyle="--",
                linewidth=1,
            )
        ax.set_ylabel("error ± uncertainty (us)")
        ax.set_xlabel("real time (ms)")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper left")
    else:
        ax.text(0.5, 0.5, "error_us missing in CSV", ha="center", va="center")
        ax.set_axis_off()

    fig.tight_layout()
    fig.savefig(args.out)
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
