#!/usr/bin/env python3
"""Plot exported measurements in separate software-path lanes."""
import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/star-vector-matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["svg.hashsalt"] = "star-vector-scale-v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    with (args.results / "measurements.csv").open() as stream:
        rows = [row for row in csv.DictReader(stream) if row["status"] == "completed"
                and row["bucket_limit"] == "1000" and row["dimensions"] == "128" and row["distribution"] == "clustered"]
    methods = [("faiss", "flat", "Faiss Flat"), ("faiss", "hnsw", "Faiss HNSW"),
               ("faiss", "ivf", "Faiss IVF-flat"), ("faiss", "pq", "Faiss IVF-PQ + rerank"),
               ("starvector", "transient", "StarVector transient C ABI")]
    databases = [("sqlite", "default", "SQLite-vec embedded"), ("qdrant", "default", "Qdrant HTTP JSON"),
                 ("starvector", "tek9", "StarVector C ABI / Tek9 worker")]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8))
    for ax, group, title in zip(axes, (methods, databases), ("Library / transient paths", "Database API paths")):
        for backend, method, label in group:
            data = sorted((int(row["records"]), float(row["p50_ms"])) for row in rows if row["backend"] == backend and row["method"] == method)
            if data:
                ax.plot([n for n, _ in data], [ms for _, ms in data], marker="o", linewidth=1.8, label=label)
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xticks([10000, 50000, 100000, 1000000, 2000000], ["10k", "50k", "100k", "1m", "2m"])
        ax.set_xlabel("Synthetic records, 128 dimensions")
        ax.set_ylabel("Median query latency (ms, log scale)")
        ax.set_title(title); ax.grid(True, alpha=0.2); ax.legend(fontsize=8, loc="best")
    fig.suptitle("Measured cosine search: latency depends on software path and recall", fontsize=14)
    fig.text(0.5, 0.02, "k=10; clustered synthetic data. Approximate recall differs—see report. Tek9 n=3; transient 1m n=20; other core rows n=1000.\n"
             "Different payload/transport/durability costs: compare lanes cautiously. Qdrant 1m readiness failed; Tek9 1m was skipped. No billion-row test.", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.10, 1, 0.95))
    fig.savefig(args.results / "latency.png", dpi=170)
    svg = args.results / "latency.svg"
    fig.savefig(svg, metadata={"Date": None})
    # Matplotlib emits padded path lines. Keep generated text diff-clean.
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    print(f"Generated latency.png and latency.svg with matplotlib {matplotlib.__version__}")


if __name__ == "__main__":
    main()
