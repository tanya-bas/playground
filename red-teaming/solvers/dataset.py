"""Dataset helpers for the slack attack eval: latest dataset, channel context enrichment."""

import json
import os
import tempfile


def latest_dataset(datasets_dir: str, default_dataset: str) -> str:
    """Return path to most recent dataset in datasets_dir, or default."""
    if not os.path.isdir(datasets_dir):
        return default_dataset
    files = [
        f for f in os.listdir(datasets_dir)
        if f.endswith(".jsonl") and os.path.isfile(os.path.join(datasets_dir, f))
    ]
    if not files:
        return default_dataset
    files.sort(reverse=True)
    return os.path.join(datasets_dir, files[0])


def enrich_dataset_with_channel_context(
    dataset_path: str,
    channel_contexts_path: str,
) -> str:
    """Enrich dataset samples with channel_context and channel_id from channel_contexts.json when missing."""
    channel_summaries = {}
    channel_ids = {}
    if os.path.isfile(channel_contexts_path):
        with open(channel_contexts_path, encoding="utf-8") as f:
            data = json.load(f)
            channel_summaries = data.get("channel_summaries", {})
            channel_ids = data.get("channel_ids", {})

    enriched = []
    with open(dataset_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            sample = json.loads(line)
            if "metadata" not in sample:
                sample["metadata"] = {}
            meta = sample["metadata"]
            target_channel = meta.get("target_channel", "")
            if "channel_context" not in meta:
                meta["channel_context"] = channel_summaries.get(target_channel, "")
            if "channel_id" not in meta:
                meta["channel_id"] = channel_ids.get(target_channel, "")
            for key in ("attack_type", "L1", "L2", "L3", "target_channel", "company_name"):
                if key not in meta:
                    meta[key] = ""
            meta["sample_id"] = sample.get("id", "")
            enriched.append(sample)

    if not enriched:
        return dataset_path

    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="eval_dataset_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for s in enriched:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        return path
    except Exception:
        os.close(fd)
        os.unlink(path)
        raise
