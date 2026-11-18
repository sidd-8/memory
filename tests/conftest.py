import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from apps.api.identity import main


@pytest.fixture
def identity_db(monkeypatch):
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Run with the Compose test service to use PostgreSQL")

    schema = f"test_identity_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            parts = urlsplit(dsn)
            query = dict(parse_qsl(parts.query))
            query["options"] = f"-csearch_path={schema}"
            monkeypatch.setenv(
                "DATABASE_URL", parts._replace(query=urlencode(query)).geturl()
            )
            command.upgrade(Config("alembic.ini"), "head")
            yield
        finally:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture
def provision(identity_db, capsys):
    def run(*args):
        main(list(args))
        return json.loads(capsys.readouterr().out)

    return run


@pytest.fixture
def issue_key(provision):
    def issue(workspace, agent, subject, *scopes):
        return provision(
            "issue-key",
            workspace,
            "--agent-id",
            agent,
            "--subject-id",
            subject,
            *(arg for scope in scopes for arg in ("--scope", scope)),
        )

    return issue


@pytest.fixture
def tenants(provision):
    def create(name):
        owner = provision(
            "create-workspace", "--name", name, "--owner", f"{name}-owner"
        )
        workspace = owner["workspace_id"]
        member = provision("add-member", workspace, "--user", f"{name}-member")
        agent = provision(
            "register-agent",
            workspace,
            "--member-id",
            member["user_id"],
            "--name",
            "Agent",
        )["agent_id"]
        subject = provision(
            "create-subject",
            workspace,
            "--kind",
            "project",
            "--reference",
            "my-project",
        )["subject_id"]
        return workspace, agent, subject

    return create("alpha"), create("beta")
