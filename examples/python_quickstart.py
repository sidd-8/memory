import argparse
from datetime import UTC, datetime
from uuid import UUID

from agent_memory import ClaimInput, MemoryClient, MemoryCreate, SourceInput

QUERY = "inventory PostgreSQL"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write or read a source-backed memory."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("write")
    read = commands.add_parser("read")
    read.add_argument("memory_id", type=UUID)
    args = parser.parse_args()

    with MemoryClient.from_env() as client:
        if args.command == "write":
            memory = client.add(
                MemoryCreate(
                    category="project",
                    content="The inventory service stores records in PostgreSQL.",
                    source=SourceInput(
                        origin="quickstart",
                        source_ref="example:inventory",
                        observed_at=datetime.now(UTC),
                        trust_class="agent_reported",
                    ),
                    claims=[
                        ClaimInput(predicate="database", object_value="PostgreSQL")
                    ],
                )
            )
            print(memory.id)
            return

        memory = client.get(args.memory_id)
        result = client.search(QUERY)
        print(memory.content)
        print(f"Source: {memory.source.source_ref} ({memory.source.id})")
        print(f"Matching evidence: {len(result.evidence)}")


if __name__ == "__main__":
    main()
