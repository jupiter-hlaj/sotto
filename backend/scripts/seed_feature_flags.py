#!/usr/bin/env python3
"""Seed initial feature flags into sotto-feature-flags-{env} DynamoDB table."""

import argparse
from datetime import datetime, timezone

import boto3

FEATURE_FLAGS = [
    {
        "flag_name": "ai_summary",
        "enabled_tiers": ["beta"],
        "default_value": False,
        "description": "AI call summarization via Bedrock",
    },
    {
        "flag_name": "epic_dom_injection",
        "enabled_tiers": ["beta", "live_test"],
        "default_value": False,
        "description": "Applied Epic DOM phone number injection",
    },
    {
        "flag_name": "caller_id_matching",
        "enabled_tiers": ["beta", "live_test", "full"],
        "default_value": True,
        "description": "Match caller to client record",
    },
    {
        "flag_name": "action_items",
        "enabled_tiers": ["beta"],
        "default_value": False,
        "description": "Extract action items from transcript",
    },
]


def seed(env: str) -> None:
    table_name = f"sotto-feature-flags-{env}"
    table = boto3.resource("dynamodb").Table(table_name)
    now = datetime.now(timezone.utc).isoformat()

    for flag in FEATURE_FLAGS:
        item = {**flag, "updated_at": now}
        table.put_item(Item=item)
        print(f"  Seeded: {flag['flag_name']} -> {flag['enabled_tiers']}")

    print(f"\nDone. {len(FEATURE_FLAGS)} flags seeded into {table_name}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Sotto feature flags")
    parser.add_argument("--env", required=True, choices=["dev", "prod"], help="Target environment")
    args = parser.parse_args()
    seed(args.env)
