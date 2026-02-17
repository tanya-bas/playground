"""Export eval results (scores, metadata) to CSV."""

import csv
import os
import sys


def write_eval_results_csv(
    run_dir: str,
    logs: list,
    dataset_path: str,
    timestamp: str,
) -> None:
    """Parse eval logs and write eval_results_{timestamp}.csv."""
    try:
        from inspect_ai.analysis import samples_df
    except ImportError:
        print("Warning: inspect_ai.analysis not available, skipping eval_results.csv", file=sys.stderr)
        return

    try:
        df = samples_df(logs)
    except Exception as e:
        print(f"Warning: Could not read log: {e}", file=sys.stderr)
        return

    conversations_dir = os.path.join(run_dir, "conversations")
    csv_path = os.path.join(run_dir, f"eval_results_{timestamp}.csv")
    dataset_path = os.path.abspath(dataset_path)

    rows = []
    for _, row in df.iterrows():
        sample_id = str(row.get("id", row.get("sample_id", "")))
        input_val = row.get("input", "")
        score = ""
        for col in df.columns:
            if "score" in col.lower() and "model_graded" in col.lower() and "stderr" not in col.lower():
                val = row.get(col)
                if val is not None and str(val).strip():
                    score = str(val)
                    break

        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            meta = {}
        for key in ("L1", "L2", "L3", "target_channel", "channel_id"):
            if key not in meta:
                meta[key] = row.get(f"metadata_{key}", "")

        conv_path = os.path.join(conversations_dir, f"{sample_id}.json")
        if not os.path.isfile(conv_path):
            conv_path = ""

        rows.append({
            "sample_id": sample_id,
            "input": input_val,
            "conversation_path": conv_path,
            "score": score,
            "L1": meta.get("L1", ""),
            "L2": meta.get("L2", ""),
            "L3": meta.get("L3", ""),
            "target_channel": meta.get("target_channel", ""),
            "channel_id": meta.get("channel_id", ""),
            "dataset_path": dataset_path,
        })

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "sample_id", "input", "conversation_path", "score",
            "L1", "L2", "L3", "target_channel", "channel_id", "dataset_path",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {csv_path}")
