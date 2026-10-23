"""
Create specialist sub-agents for a swarm.

Customize the example specialists with the roles and prompts needed by your
workflow. Skills can be uploaded separately by upload_skills.py.

Saves the resulting agent IDs to .specialist_ids.json so create_coordinator.py
can reference them.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python create_specialists.py
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic


SPECIALISTS = [
    {
        "key": "specialist_one",
        "name": "Research Specialist",
        "model": "claude-sonnet-4-6",
        "system": (
            "You are a research specialist. Investigate the part of the user's "
            "task assigned to you. Return concise, relevant findings, explain "
            "your reasoning, and identify any uncertainty or missing information."
        ),
    },
    {
        "key": "specialist_two",
        "name": "Analysis Specialist",
        "model": "claude-sonnet-4-6",
        "system": (
            "You are an analysis specialist. Analyze the part of the user's "
            "task assigned to you. Return a structured assessment with key "
            "conclusions, supporting evidence, and any risks or open questions."
        ),
    },
]


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    client = Anthropic(
        api_key=api_key,
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )

    specialist_ids: dict[str, str] = {}
    for spec in SPECIALISTS:
        agent = client.beta.agents.create(
            name=spec["name"],
            model=spec["model"],
            system=spec["system"],
            tools=[{"type": "agent_toolset_20260401"}],
            metadata={
                "role": spec["key"],
            },
        )
        specialist_ids[spec["key"]] = agent.id
        print(f"  Created {spec['name']:32s} -> {agent.id}")

    Path(".specialist_ids.json").write_text(json.dumps(specialist_ids, indent=2))
    print(f"\nSaved {len(specialist_ids)} specialist IDs to .specialist_ids.json")
    print("Next: python create_coordinator.py")


if __name__ == "__main__":
    main()
