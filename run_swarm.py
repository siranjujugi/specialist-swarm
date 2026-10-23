"""Run a task with the configured coordinator and stream session events.

Usage:
    python run_swarm.py "Describe the task for the swarm"
    python run_swarm.py "Task instructions" --context path/to/context.txt
"""

import argparse
import os
from pathlib import Path

from anthropic import Anthropic


OUTPUT_DIR = Path("outputs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a task with the specialist swarm.")
    parser.add_argument("task", help="Instructions describing the task for the swarm.")
    parser.add_argument(
        "--context",
        type=Path,
        help="Optional text file to include as task context.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    coordinator_path = Path(".coordinator_id")
    environment_path = Path(".environment_id")
    if not coordinator_path.exists() or not environment_path.exists():
        raise SystemExit(
            "Missing .coordinator_id or .environment_id. Run "
            "setup_environment.py, create_specialists.py, and "
            "create_coordinator.py first."
        )

    task = args.task
    if args.context:
        task += f"\n\n===== CONTEXT: {args.context.name} =====\n{args.context.read_text()}"

    coordinator_id = coordinator_path.read_text().strip()
    environment_id = environment_path.read_text().strip()
    client = Anthropic()

    print(f"Starting session against coordinator {coordinator_id}...")
    session = client.beta.sessions.create(
        agent=coordinator_id,
        environment_id=environment_id,
        title=f"Swarm session: {args.task[:60]}",
    )
    Path(".last_session_id").write_text(session.id)

    print("\n=== EVENT STREAM ===\n")
    final_text_parts: list[str] = []

    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": task}],
                }
            ],
        )
        for event in stream:
            event_type = event.type
            if event_type == "session.thread_created":
                print(f"  [thread spawned]   {event.agent_name}", flush=True)
            elif event_type == "session.thread_status_running":
                name = getattr(event, "agent_name", "?")
                print(f"  [thread running]   {name}", flush=True)
            elif event_type == "agent.thread_message_received":
                print(f"  [reply <-]          {event.from_agent_name}", flush=True)
            elif event_type == "agent.thread_message_sent":
                print(f"  [delegate ->]       {event.to_agent_name}", flush=True)
            elif event_type == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        final_text_parts.append(block.text)
                        print(block.text, end="", flush=True)
            elif event_type == "agent.tool_use":
                print(f"\n  [tool: {getattr(event, 'name', '?')}]", flush=True)
            elif event_type == "session.status_idle":
                print("\n\n[swarm finished]")
                break

    OUTPUT_DIR.mkdir(exist_ok=True)
    transcript_path = OUTPUT_DIR / "coordinator-transcript.txt"
    transcript_path.write_text("".join(final_text_parts))
    print(f"\nCoordinator transcript saved to {transcript_path}")

    print("\nDownloading session files...")
    files = client.beta.files.list(
        scope_id=session.id,
        betas=["managed-agents-2026-04-01"],
    )
    file_count = 0
    for file in files.data:
        output_path = OUTPUT_DIR / file.filename
        print(f"  {file.filename} -> {output_path}")
        content = client.beta.files.download(file.id)
        content.write_to_file(str(output_path))
        file_count += 1

    if file_count == 0:
        print("  (no files found)")
    else:
        print(f"\nDownloaded {file_count} file(s) to {OUTPUT_DIR}/")

    print("\nView the full session at:")
    print(f"  https://platform.claude.com/sessions/{session.id}")


if __name__ == "__main__":
    main()
