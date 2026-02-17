#!/usr/bin/env python3
"""
Generate Inspect AI–compatible attack dataset from attack taxonomy CSV.

Reads attack_taxonomy.csv, generates N attack message variants per attack via LLM,
then generates unique grading criteria per row. Writes to datasets/{timestamp}.jsonl for use with run_eval.py.

Structure: 3 attacks (CSV rows) × N variants = 3N rows.
- Attacks are generated at L3 only; L1 and L2 serve as context (tree structure).
- Each row: attack prompt (input) + unique grading criteria (target).
- At n=2: 6 entries, 12 LLM calls (6 attack + 6 target).

Usage:
  python attack_data_generation/generate_attack_dataset.py [--variants N] [--output path]

Requires: ANTHROPIC_API_KEY
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATASETS_DIR = os.path.join(_SCRIPT_DIR, "datasets")

from dotenv import load_dotenv
import anthropic

from attack_prompts import build_attack_generation_prompt, build_target_generation_prompt

load_dotenv(os.path.join(os.path.dirname(_SCRIPT_DIR), ".env"))

DEFAULT_TAXONOMY = os.path.join(_SCRIPT_DIR, "attack_taxonomy.csv")
DEFAULT_VARIANTS = 2
CHANNEL_CONTEXTS_PATH = os.path.join(
    os.path.dirname(_SCRIPT_DIR), "slack_data_generation", "channel_contexts.json"
)


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


def generate_attack_message(
    client: anthropic.Anthropic,
    attack: dict,
    level: str,
    company_context: str,
    target_channel: str,
    channel_context: str,
    previous_variants: list[str] | None = None,
) -> str:
    """Generate one attack message for (attack, level)."""
    system, user = build_attack_generation_prompt(
        attack,
        level,
        company_context=company_context,
        target_channel=target_channel,
        channel_context=channel_context,
        previous_variants=previous_variants,
    )
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = (resp.content[0].text if resp.content else "").strip()
    for prefix in ('"', "'"):
        if text.startswith(prefix) and text.endswith(prefix):
            text = text[1:-1]
    return text.strip() if len(text) > 10 else ""


def generate_target_criteria(
    client: anthropic.Anthropic,
    attack: dict,
    attack_prompt: str,
) -> str:
    """Generate grading criteria for a specific attack prompt."""
    system, user = build_target_generation_prompt(attack, attack_prompt)
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return (resp.content[0].text if resp.content else "").strip()


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
    args = parser.parse_args()

    if not os.path.isfile(args.taxonomy):
        print(f"Error: taxonomy file not found: {args.taxonomy}")
        return 1

    if not os.path.isfile(CHANNEL_CONTEXTS_PATH):
        print(f"Error: channel contexts not found: {CHANNEL_CONTEXTS_PATH}")
        return 1

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
    company_name = contexts.get("company_name", "Unknown")
    channel_summaries = contexts.get("channel_summaries", {})
    channel_names = list(channel_summaries.keys()) or ["general"]

    samples = []
    sample_id = 1
    channel_idx = 0

    print(f"Generating {len(attacks)} attacks × {args.variants} variants = {len(attacks) * args.variants} rows ({2 * len(attacks) * args.variants} LLM calls)", flush=True)
    print(f"Company: {company_name} | Channels: {channel_names}", flush=True)

    for a_idx, attack in enumerate(attacks):
        attack_id = (
            f"{attack.get('L1 Category','')}_{attack.get('L2 Category','')}_{attack.get('L3 Category','')}".replace(
                " ", "_"
            )
        )
        print(f"  Attack {a_idx + 1}/{len(attacks)}: {attack_id}", flush=True)
        variants = []
        for v_idx in range(args.variants):
            target_channel = channel_names[channel_idx % len(channel_names)]
            channel_context = channel_summaries.get(target_channel, "")
            channel_idx += 1

            print(f"    variant {v_idx + 1}/{args.variants} (attack message, target=#{target_channel})...", flush=True)
            attack_msg = generate_attack_message(
                client,
                attack,
                "L3",
                company_context=company_context,
                target_channel=target_channel,
                channel_context=channel_context,
                previous_variants=variants,
            )
            if not attack_msg:
                print(f"      (skipped empty response)", flush=True)
                continue
            print(f"    variant {v_idx + 1}/{args.variants} (target criteria)...", flush=True)
            target = generate_target_criteria(client, attack, attack_msg)
            samples.append({
                "id": f"attack_{sample_id}",
                "input": attack_msg,
                "target": target,
                "metadata": {
                    "attack_type": attack_id,
                    "L1": attack.get("L1 Category"),
                    "L2": attack.get("L2 Category"),
                    "L3": attack.get("L3 Category"),
                    "company_name": company_name,
                    "target_channel": target_channel,
                    "channel_context": channel_context,
                },
            })
            variants.append(attack_msg)
            sample_id += 1
        print(f"  {attack_id}: done ({len(variants)} variants)", flush=True)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"Done. Wrote {len(samples)} samples to {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
