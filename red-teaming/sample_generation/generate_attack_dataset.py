#!/usr/bin/env python3
"""
Generate Inspect AI–compatible attack dataset from attack taxonomy CSV.

Reads attack_taxonomy.csv, generates N attack message variants per (risk, channel) via LLM.
Writes to datasets/{timestamp}.jsonl for use with run_eval.py. Grading uses the core rubric + attack/channel context.

Structure: num_attacks × num_channels × N variants (social channel excluded).
- Attacks are generated at L3 only; L1 and L2 serve as context (tree structure).
- Each row: attack prompt (input) + metadata (attack_type, L1, L2, L3, Helper, target_channel, etc.).
- Example: 3 attacks × 5 channels × 2 variants = 30 rows, 30 LLM calls.

Duplicate prevention: Within each (attack, channel) pair, previous_variants is passed to the
LLM so it avoids repeating already-generated messages. No cross-channel or cross-attack
deduplication—we rely on the prompt ("Avoid repeating these previous variants").

Usage:
  python sample_generation/generate_attack_dataset.py [--variants N] [--output path] [--max-workers N]

Requires: ANTHROPIC_API_KEY
"""

import argparse
import csv
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
DATASETS_DIR = os.path.join(_SCRIPT_DIR, "datasets")

from dotenv import load_dotenv
import anthropic

from sample_generation.attack_prompts import build_attack_generation_prompt

load_dotenv(os.path.join(os.path.dirname(_SCRIPT_DIR), ".env"))

DEFAULT_TAXONOMY = os.path.join(_SCRIPT_DIR, "attack_taxonomy.csv")
DEFAULT_VARIANTS = 2
CHANNEL_CONTEXTS_PATH = os.path.join(
    os.path.dirname(_SCRIPT_DIR), "environment_generation", "channel_contexts.json"
)
LEAKABLE_DATA_PATH = os.path.join(_SCRIPT_DIR, "datasets", "leakable_data.json")


def load_taxonomy(path: str) -> list[dict]:
    """Load attack taxonomy from CSV."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def load_channel_contexts(path: str) -> dict:
    """Load company and channel context from channel_contexts.json."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_leakable_data(path: str) -> dict:
    """Load leakable data from leakable_data.json. Returns {} if file missing."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Strip _meta key
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}


def generate_attack_message(
    client: anthropic.Anthropic,
    attack: dict,
    level: str,
    company_context: str,
    target_channel: str,
    channel_context: str,
    channel_id: str = "",
    leakable_items: list | None = None,
    previous_variants: list[str] | None = None,
) -> str:
    """Generate one attack message for (attack, level)."""
    system, user = build_attack_generation_prompt(
        attack,
        level,
        company_context=company_context,
        target_channel=target_channel,
        channel_context=channel_context,
        channel_id=channel_id,
        leakable_items=leakable_items,
        previous_variants=previous_variants,
    )
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = (resp.content[0].text if resp.content else "").strip()
    for prefix in ('"', "'"):
        if text.startswith(prefix) and text.endswith(prefix):
            text = text[1:-1]
    return text.strip() if len(text) > 10 else ""


def main():
    parser = argparse.ArgumentParser(
        description="Generate attack dataset from taxonomy CSV"
    )
    parser.add_argument(
        "--taxonomy",
        default=DEFAULT_TAXONOMY,
        help=f"Path to attack taxonomy CSV (default: {DEFAULT_TAXONOMY})",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output dataset path (default: datasets/{timestamp}.jsonl)",
    )
    parser.add_argument(
        "--variants",
        type=int,
        default=DEFAULT_VARIANTS,
        help=f"Variants per attack (default: {DEFAULT_VARIANTS})",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Max parallel LLM calls (default: 10). Lower if hitting rate limits.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.taxonomy):
        print(f"Error: taxonomy file not found: {args.taxonomy}")
        return 1

    if not os.path.isfile(CHANNEL_CONTEXTS_PATH):
        print(f"Error: channel contexts not found: {CHANNEL_CONTEXTS_PATH}")
        return 1

    leakable_data = load_leakable_data(LEAKABLE_DATA_PATH)
    if not leakable_data:
        print(f"Warning: datasets/leakable_data.json not found. Run: python sample_generation/extract_leakable_data.py")
        print("  Proceeding without leakable data context.", flush=True)

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("Error: ANTHROPIC_API_KEY required")
        return 1

    output_path = args.output
    if output_path is None:
        os.makedirs(DATASETS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_path = os.path.join(DATASETS_DIR, f"{timestamp}.jsonl")

    client = anthropic.Anthropic()
    attacks = load_taxonomy(args.taxonomy)
    contexts = load_channel_contexts(CHANNEL_CONTEXTS_PATH)
    company_context = contexts.get("company", "")
    channel_summaries = contexts.get("channel_summaries", {})
    channel_ids = contexts.get("channel_ids", {})
    # Channels for attack generation (social excluded—casual/non-work)
    ATTACK_CHANNELS = ["engineering", "general", "hr-confidential", "legal", "sales"]
    channel_names = [c for c in ATTACK_CHANNELS if c in channel_summaries]

    total_rows = len(attacks) * len(channel_names) * args.variants
    num_pairs = len(attacks) * len(channel_names)

    print(f"Generating {len(attacks)} attacks × {len(channel_names)} channels × {args.variants} variants = {total_rows} rows ({total_rows} LLM calls)", flush=True)
    print(f"Channels (excl. social): {channel_names}", flush=True)
    print(f"Parallel workers: {args.max_workers}", flush=True)

    # Build (attack, channel) pairs with context
    pairs: list[tuple[int, dict, str, str, str, list]] = []
    for a_idx, attack in enumerate(attacks):
        for target_channel in channel_names:
            channel_context = channel_summaries.get(target_channel, "")
            channel_id = channel_ids.get(target_channel, "")
            all_leakable = leakable_data.get(target_channel, [])
            leakable_items = random.sample(all_leakable, min(5, len(all_leakable))) if all_leakable else []
            pairs.append((a_idx, attack, target_channel, channel_context, channel_id, leakable_items))

    # variants_by_pair[pair_idx] = list of generated attack messages for that pair
    variants_by_pair: list[list[str]] = [[] for _ in pairs]

    def _generate_one(pair_idx: int, v_idx: int) -> tuple[int, int, str | None]:
        """Generate one variant. Returns (pair_idx, v_idx, attack_msg or None)."""
        a_idx, attack, target_channel, channel_context, channel_id, leakable_items = pairs[pair_idx]
        previous = variants_by_pair[pair_idx]
        msg = generate_attack_message(
            client,
            attack,
            "L3",
            company_context=company_context,
            target_channel=target_channel,
            channel_context=channel_context,
            channel_id=channel_id,
            leakable_items=leakable_items,
            previous_variants=previous,
        )
        return (pair_idx, v_idx, msg if msg else None)

    for v_idx in range(args.variants):
        print(f"  Variant {v_idx + 1}/{args.variants}: running {num_pairs} LLM calls in parallel...", flush=True)
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {executor.submit(_generate_one, p_idx, v_idx): p_idx for p_idx in range(num_pairs)}
            for future in as_completed(futures):
                p_idx, _, attack_msg = future.result()
                if attack_msg:
                    variants_by_pair[p_idx].append(attack_msg)

    # Build samples in original (attack, channel, variant) order
    samples = []
    sample_id = 1
    for p_idx in range(num_pairs):
        a_idx, attack, target_channel, channel_context, channel_id, _ = pairs[p_idx]
        attack_id = (
            f"{attack.get('L1 Category','')}_{attack.get('L2 Category','')}_{attack.get('L3 Category','')}".replace(
                " ", "_"
            )
        )
        for attack_msg in variants_by_pair[p_idx]:
            samples.append({
                "id": f"attack_{sample_id}",
                "input": attack_msg,
                "metadata": {
                    "attack_type": attack_id,
                    "L1": attack.get("L1 Category"),
                    "L2": attack.get("L2 Category"),
                    "L3": attack.get("L3 Category"),
                    "Helper": attack.get("Helper", ""),
                    "target_channel": target_channel,
                    "channel_id": channel_id,
                    "channel_context": channel_context,
                },
            })
            sample_id += 1

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"Done. Wrote {len(samples)} samples to {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
