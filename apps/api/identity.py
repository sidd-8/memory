import argparse
import json
import secrets
from uuid import UUID

from psycopg import IntegrityError

from apps.api.auth import Scope, token_digest
from apps.api.db import connect


def _nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _add_member(db, workspace_id: UUID, identifier: str, role: str):
    user = db.execute(
        """
        INSERT INTO users (identifier) VALUES (%s)
        ON CONFLICT (identifier) DO UPDATE SET identifier = EXCLUDED.identifier
        RETURNING id AS user_id
        """,
        (identifier,),
    ).fetchone()
    return db.execute(
        """
        INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (%s, %s, %s)
        ON CONFLICT (workspace_id, user_id) DO NOTHING
        RETURNING workspace_id, user_id, role
        """,
        (workspace_id, user["user_id"], role),
    ).fetchone()


def _execute(db, args):
    if args.command == "create-workspace":
        workspace = db.execute(
            "INSERT INTO workspaces (name) VALUES (%s) RETURNING id",
            (args.name,),
        ).fetchone()
        return _add_member(db, workspace["id"], args.owner, "owner")
    if args.command == "add-member":
        return _add_member(db, args.workspace_id, args.user, "member")
    if args.command == "register-agent":
        return db.execute(
            """
            INSERT INTO agents (workspace_id, registered_by, name) VALUES (%s, %s, %s)
            RETURNING id AS agent_id
            """,
            (args.workspace_id, args.member_id, args.name),
        ).fetchone()
    if args.command == "create-subject":
        return db.execute(
            """
            INSERT INTO subjects (workspace_id, kind, reference) VALUES (%s, %s, %s)
            RETURNING id AS subject_id
            """,
            (args.workspace_id, args.kind, args.reference),
        ).fetchone()
    if args.command == "issue-key":
        token = "am_" + secrets.token_urlsafe(32)
        key = db.execute(
            """
            INSERT INTO api_keys (workspace_id, agent_id, subject_id, token_digest, scopes)
            VALUES (%s, %s, %s, %s, %s) RETURNING id AS key_id
            """,
            (
                args.workspace_id,
                args.agent_id,
                args.subject_id,
                token_digest(token),
                sorted(set(args.scope)),
            ),
        ).fetchone()
        return {**key, "api_key": token}

    return db.execute(
        """
        UPDATE api_keys SET revoked_at = COALESCE(revoked_at, now())
        WHERE workspace_id = %s AND id = %s RETURNING id AS key_id
        """,
        (args.workspace_id, args.key_id),
    ).fetchone()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Provision Agent Memory identities")
    commands = parser.add_subparsers(dest="command", required=True)

    workspace = commands.add_parser("create-workspace")
    workspace.add_argument("--name", type=_nonempty, required=True)
    workspace.add_argument("--owner", type=_nonempty, required=True)

    member = commands.add_parser("add-member")
    member.add_argument("workspace_id", type=UUID)
    member.add_argument("--user", type=_nonempty, required=True)

    agent = commands.add_parser("register-agent")
    agent.add_argument("workspace_id", type=UUID)
    agent.add_argument("--member-id", type=UUID, required=True)
    agent.add_argument("--name", type=_nonempty, required=True)

    subject = commands.add_parser("create-subject")
    subject.add_argument("workspace_id", type=UUID)
    subject.add_argument("--kind", choices=("user", "project", "team"), required=True)
    subject.add_argument("--reference", type=_nonempty, required=True)

    key = commands.add_parser("issue-key")
    key.add_argument("workspace_id", type=UUID)
    key.add_argument("--agent-id", type=UUID, required=True)
    key.add_argument("--subject-id", type=UUID, required=True)
    key.add_argument("--scope", choices=list(Scope), action="append", required=True)

    revoke = commands.add_parser("revoke-key")
    revoke.add_argument("workspace_id", type=UUID)
    revoke.add_argument("key_id", type=UUID)

    args = parser.parse_args(argv)
    try:
        with connect() as db:
            result = _execute(db, args)
    except IntegrityError:
        parser.exit(
            1, "Provisioning rejected by workspace or uniqueness constraints.\n"
        )
    if result is None:
        parser.exit(1, "No matching key to revoke or user already a member.\n")
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
