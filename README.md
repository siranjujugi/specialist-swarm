# Specialist Swarm

A starter template for building a coordinator-and-specialists workflow with
Anthropic Managed Agents. The scripts preserve the API setup and orchestration
patterns; customize the agents, skills, task input, and deliverables for your
own demo.

## Requirements

- Python 3.10+
- An Anthropic API key with access to Managed Agents

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-api-key"
```

## Build and run

1. **Provision an environment.** Run `python setup_environment.py`. The
   environment ID is saved to `.environment_id` and reused on later runs.
2. **Create specialists.** Customize the `SPECIALISTS` list in
   [`create_specialists.py`](./create_specialists.py), then run
   `python create_specialists.py`. IDs are saved to `.specialist_ids.json`.
3. **Create the coordinator.** Customize `COORDINATOR_SYSTEM` and the
   coordinator name/model in [`create_coordinator.py`](./create_coordinator.py),
   then run `python create_coordinator.py`. Its ID is saved to
   `.coordinator_id`.
4. **Add optional skills.** Create skill directories under `skills/`, each
   containing a `SKILL.md` with YAML frontmatter (`name` and `description`).
   Map each directory to a specialist key in `SKILL_TO_SPECIALIST` in
   [`upload_skills.py`](./upload_skills.py), then run `python upload_skills.py`.
5. **Run a task.** Run `python run_swarm.py "Describe the task for the swarm"`
   and optionally include a context file with
   `python run_swarm.py "Task instructions" --context path/to/context.txt`.
   The event stream is printed and the coordinator transcript is saved under
   `outputs/`.
6. **Download session files later if needed.** Run
   `python download_deliverable.py [session_id]`. If no ID is provided, it
   uses `.last_session_id`.

## Customize the template

- Replace the generic specialist entries and system prompts in
  `create_specialists.py`.
- Define the swarm's responsibilities, delegation strategy, and output
  requirements in `create_coordinator.py`.
- Add domain-specific skills under `skills/` and map them to specialist keys.
- Add any task-specific input files or output handling to `run_swarm.py`.
- Adjust environment configuration in `setup_environment.py` to suit your
  workspace's access and networking requirements.

## Project files

```text
.
├── README.md
├── requirements.txt
├── create_specialists.py       # Creates specialist agents
├── create_coordinator.py       # Creates the coordinator agent
├── setup_environment.py        # Provisions a Managed Agents environment
├── upload_skills.py            # Uploads and attaches custom skills
├── run_swarm.py                # Runs a task and streams session events
├── download_deliverable.py     # Downloads files from a session
└── skills/                     # Add custom skill directories here
```

See the [Managed Agents documentation](https://platform.claude.com/docs/en/managed-agents/multi-agent)
and [Agent Skills documentation](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
for API details.
