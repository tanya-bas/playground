"""Export eval results (scores, metadata) to CSV."""

import csv
import json
import os
import sys
import zipfile


def _get_conversation_from_log(log_path: str, sample_id: str, epoch: int) -> str:
    """Extract conversation (model output) from eval log zip."""
    if not log_path or not os.path.isfile(log_path):
        return ""
    try:
        sample_name = f"samples/{sample_id}_epoch_{epoch}.json"
        with zipfile.ZipFile(log_path, "r") as zf:
            if sample_name not in zf.namelist():
                return ""
            with zf.open(sample_name) as f:
                data = json.load(f)
        choices = data.get("output", {}).get("choices", [])
        if not choices:
            return ""
        msg = choices[0].get("message", {})
        return msg.get("content", "") or ""
    except Exception:
        return ""


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

        # Extract conversation from eval log (embedded in CSV)
        log_path = row.get("log", "")
        if hasattr(log_path, "path"):
            log_path = getattr(log_path, "path", log_path)
        epoch = int(row.get("epoch", 1))
        conversation = _get_conversation_from_log(str(log_path) if log_path else "", sample_id, epoch)

        rows.append({
            "sample_id": sample_id,
            "input": input_val,
            "conversation": conversation,
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
            "sample_id", "input", "conversation", "score",
            "L1", "L2", "L3", "target_channel", "channel_id", "dataset_path",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {csv_path}")
