"""
Create the coordinator agent that orchestrates a specialist swarm.

The coordinator's roster comes from create_specialists.py. Customize its
instructions below to define how it should delegate and assemble the result.

Saves the coordinator's ID to .coordinator_id.

Usage:
    python create_coordinator.py
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic


COORDINATOR_SYSTEM = """\
You are a coordinator agent. Your job is to understand the user's task,
delegate well-scoped parts of it to the available specialists, and assemble
their contributions into a coherent, useful result.

Decide which specialists are relevant and whether their work can happen in
parallel. Give each specialist the task context, a clear request, and the
expected shape of its response. Avoid asking multiple specialists to do the
same work unless independent perspectives are useful.

Review the responses for consistency, address important gaps with follow-up
delegation when needed, and return the completed result in the format requested
by the user. Be clear about assumptions or unresolved limitations.
"""


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    specialist_ids_path = Path(".specialist_ids.json")
    if not specialist_ids_path.exists():
        raise SystemExit("Run create_specialists.py first.")
    specialist_ids = json.loads(specialist_ids_path.read_text())

    client = Anthropic(
        api_key=api_key,
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )

    coordinator = client.beta.agents.create(
        name="Swarm Coordinator",
        model="claude-opus-4-7",
        system=COORDINATOR_SYSTEM,
        tools=[{"type": "agent_toolset_20260401"}],
        multiagent={
            "type": "coordinator",
            "agents": [
                {"type": "agent", "id": agent_id}
                for agent_id in specialist_ids.values()
            ],
        },
        metadata={"role": "coordinator"},
    )

    Path(".coordinator_id").write_text(coordinator.id)
    print(f"Coordinator created: {coordinator.id}")
    print(f"Roster: {list(specialist_ids.keys())}")
    print("\nNext: python upload_skills.py (if using skills), then python run_swarm.py")


if __name__ == "__main__":
    main()
