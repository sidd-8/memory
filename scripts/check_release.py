"""Run the retrieval baseline and backup drill against a disposable Compose project."""

import argparse
import json
import math
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter, sleep
from uuid import uuid4

import httpx2
from agent_memory import MemoryClient, MemoryCreate, NotFound, SourceInput

ROOT = Path(__file__).resolve().parents[1]


def run_check(ollama_url: str) -> dict:
    project = f"memory-release-{uuid4().hex[:8]}"
    env = {**os.environ, "API_PORT": "0", "OLLAMA_URL": ollama_url}

    def compose(*args, **options):
        return subprocess.run(
            ["docker", "compose", "-p", project, *args],
            cwd=ROOT,
            env=env,
            check=True,
            **options,
        )

    def identity(*args):
        command = ("exec", "-T", "api", "python", "-m", "apps.api.identity")
        return json.loads(compose(*command, *args, capture_output=True).stdout)

    def database(*args, **options):
        return compose("exec", "-T", "postgres", *args, **options)

    def sql(statement, database_name="memory"):
        return database(
            "psql",
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "memory",
            "-d",
            database_name,
            "-c",
            statement,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def base_url():
        address = compose(
            "port", "api", "8000", capture_output=True, text=True
        ).stdout.strip()
        return f"http://{address}"

    try:
        compose(
            "--progress",
            "quiet",
            "up",
            "--wait",
            "--wait-timeout",
            "90",
            "api",
            "worker",
        )
        owner = identity(
            "create-workspace", "--name", "Release check", "--owner", "operator"
        )
        workspace = owner["workspace_id"]
        subject = identity(
            "create-subject",
            workspace,
            "--kind",
            "project",
            "--reference",
            "release-check",
        )["subject_id"]

        def issue(name, *scopes):
            agent = identity(
                "register-agent",
                workspace,
                "--member-id",
                owner["user_id"],
                "--name",
                name,
            )
            return identity(
                "issue-key",
                workspace,
                "--agent-id",
                agent["agent_id"],
                "--subject-id",
                subject,
                *(arg for scope in scopes for arg in ("--scope", scope)),
            )["api_key"]

        writer = issue("Writer", "memory:write", "memory:delete")
        reader = issue("Reader", "memory:read")
        settings = {"workspace_id": workspace, "subject_id": subject}
        dataset = json.loads((ROOT / "tests/fixtures/retrieval.json").read_text())
        records = dataset["cases"]
        distractors = dataset["distractors"]
        source = SourceInput(
            origin="release-check",
            source_ref="fixture:retrieval",
            observed_at=datetime.now(UTC),
        )
        ids = {}
        with MemoryClient(base_url=base_url(), api_key=writer, **settings) as client:
            for record in records:
                ids[record["id"]] = client.add(
                    MemoryCreate(
                        category="project",
                        content=record["content"],
                        source=source,
                    )
                ).id
            for content in distractors:
                client.add(
                    MemoryCreate(category="project", content=content, source=source)
                )

        total = len(records) + len(distractors)
        deadline = monotonic() + 240
        while (
            int(
                sql(f"""
            SELECT count(*) FROM memories
            WHERE workspace_id = '{workspace}' AND subject_id = '{subject}' AND embedding IS NOT NULL
        """)
            )
            != total
        ):
            if monotonic() >= deadline:
                raise RuntimeError(
                    "Indexing did not finish. Check the worker and Ollama connectivity."
                )
            sleep(2)

        compose("restart", "api")
        compose("--progress", "quiet", "up", "--wait", "--wait-timeout", "30", "api")
        address = base_url()
        first = records[0]
        expected = ids[first["id"]]
        with MemoryClient(base_url=address, api_key=reader, **settings) as client:
            assert client.get(expected).content == first["content"]

        latencies = []
        recall = 0
        misses = []
        with httpx2.Client(
            base_url=address,
            headers={"Authorization": "Bearer " + reader},
            trust_env=False,
            timeout=20,
        ) as client:

            def retrieve(query):
                start = perf_counter()
                response = client.post(
                    "/v1/memories/search",
                    params=settings,
                    json={"query": query, "limit": 5},
                )
                elapsed = (perf_counter() - start) * 1000
                response.raise_for_status()
                result = response.json()
                if result["mode"] != "hybrid":
                    raise RuntimeError("Hybrid retrieval unavailable")
                model_ms = float(
                    re.fullmatch(
                        r"embedding;dur=(\d+(?:\.\d+)?)",
                        response.headers["server-timing"],
                    )[1]
                )
                return result, max(0, elapsed - model_ms)

            retrieve(first["queries"][0])
            for record in records:
                for query in record["queries"]:
                    result, duration = retrieve(query)
                    latencies.append(duration)
                    if str(ids[record["id"]]) in {
                        hit["memory_id"] for hit in result["evidence"]
                    }:
                        recall += 1
                    else:
                        misses.append(query)

        with TemporaryDirectory(prefix="memory-backup-") as directory:
            backup = Path(directory) / "memory.dump"
            with backup.open("wb") as stream:
                database("pg_dump", "-U", "memory", "-Fc", "memory", stdout=stream)
            database("createdb", "-U", "memory", "-T", "template0", "restore_check")
            with backup.open("rb") as stream:
                database(
                    "pg_restore",
                    "-U",
                    "memory",
                    "--exit-on-error",
                    "--single-transaction",
                    "-d",
                    "restore_check",
                    stdin=stream,
                )
            assert sql(
                "SELECT version_num FROM alembic_version", "restore_check"
            ) == sql("SELECT version_num FROM alembic_version")
            assert (
                sql(
                    f"SELECT content FROM memories WHERE id = '{expected}'",
                    "restore_check",
                )
                == first["content"]
            )
            assert sql(
                f"SELECT count(*) FROM memories WHERE workspace_id = '{workspace}'",
                "restore_check",
            ) == str(total)

        with MemoryClient(base_url=address, api_key=writer, **settings) as client:
            client.delete(expected)
        with MemoryClient(base_url=address, api_key=reader, **settings) as client:
            try:
                client.get(expected)
            except NotFound:
                pass
            else:
                raise AssertionError("Deleted memory still accessible")
            assert expected not in {
                hit.memory_id for hit in client.search(first["queries"][0]).evidence
            }
        assert sql(f"SELECT count(*) FROM memories WHERE id = '{expected}'") == "0"

        p95 = sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1]
        return {
            "memories": total,
            "queries": len(latencies),
            "recall_at_5": recall / len(latencies),
            "p95_ms_excluding_embedding": round(p95, 2),
            "misses": misses,
            "restart_persistence": True,
            "backup_restore": True,
            "deletion": True,
        }
    finally:
        compose("--progress", "quiet", "down", "--volumes", capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ollama-url", default="http://host.lima.internal:11434")
    args = parser.parse_args()
    result = run_check(args.ollama_url)
    print(json.dumps(result, indent=2))
    if (
        result["queries"] != 100
        or result["recall_at_5"] < 0.90
        or result["p95_ms_excluding_embedding"] >= 700
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
