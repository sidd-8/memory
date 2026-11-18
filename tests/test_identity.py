from hashlib import sha256
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from apps.api.auth import AgentAccess, Scope, require_subject
from apps.api.db import connect
from apps.api.main import app


def subject_path(workspace, subject):
    return f"/v1/workspaces/{workspace}/subjects/{subject}"


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_two_agents_can_read_same_authorized_subject(provision, tenants, issue_key):
    (workspace, first_agent, subject), _ = tenants
    member = provision("add-member", workspace, "--user", "second-agent-operator")
    second_agent = provision(
        "register-agent",
        workspace,
        "--member-id",
        member["user_id"],
        "--name",
        "Second Agent",
    )["agent_id"]

    with TestClient(app) as client:
        for agent in (first_agent, second_agent):
            key = issue_key(workspace, agent, subject, Scope.SUBJECT_READ)
            response = client.get(
                subject_path(workspace, subject), headers=bearer(key["api_key"])
            )
            assert response.status_code == 200
            assert response.json() == {
                "workspace_id": workspace,
                "id": subject,
                "kind": "project",
                "reference": "my-project",
            }


def test_subject_endpoint_enforces_workspace_subject_and_scope(
    provision, tenants, issue_key
):
    (workspace, agent, subject), (other_workspace, _, other_subject) = tenants
    same_workspace_subject = provision(
        "create-subject", workspace, "--kind", "team", "--reference", "another-subject"
    )["subject_id"]
    valid_key = issue_key(workspace, agent, subject, Scope.SUBJECT_READ)
    wrong_scope = issue_key(workspace, agent, subject, Scope.GRAPH_READ)

    with TestClient(app) as client:
        for target_workspace, target_subject, key in (
            (other_workspace, other_subject, valid_key),
            (other_workspace, subject, valid_key),
            (workspace, other_subject, valid_key),
            (workspace, same_workspace_subject, valid_key),
            (workspace, str(uuid4()), valid_key),
            (workspace, subject, wrong_scope),
        ):
            response = client.get(
                subject_path(target_workspace, target_subject),
                headers=bearer(key["api_key"]),
            )
            assert response.status_code == 403
            assert response.json() == {"detail": "Access denied"}


def test_only_digest_is_stored_and_revocation_invalidates_key(
    provision, tenants, issue_key
):
    (workspace, agent, subject), (other_workspace, _, _) = tenants
    key = issue_key(workspace, agent, subject, Scope.SUBJECT_READ)
    token = key["api_key"]
    with connect() as db:
        stored = db.execute(
            "SELECT token_digest FROM api_keys WHERE workspace_id = %s AND id = %s",
            (workspace, key["key_id"]),
        ).fetchone()
        assert stored == {"token_digest": sha256(token.encode()).digest()}

    path = subject_path(workspace, subject)
    with TestClient(app) as client:
        with pytest.raises(SystemExit) as error:
            provision("revoke-key", other_workspace, key["key_id"])
        assert error.value.code == 1
        assert client.get(path, headers=bearer(token)).status_code == 200

        provision("revoke-key", workspace, key["key_id"])
        for headers in ({}, bearer("am_unknown"), bearer(token)):
            response = client.get(path, headers=headers)
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"


def test_database_rejects_cross_workspace_provisioning(provision, tenants, issue_key):
    (workspace, agent, subject), (_, other_agent, other_subject) = tenants
    member = provision("add-member", tenants[1][0], "--user", "other-agent-operator")

    with pytest.raises(SystemExit) as error:
        provision(
            "register-agent",
            workspace,
            "--member-id",
            member["user_id"],
            "--name",
            "Mixed",
        )
    assert error.value.code == 1
    for key_agent, key_subject in ((agent, other_subject), (other_agent, subject)):
        with pytest.raises(SystemExit) as error:
            issue_key(workspace, key_agent, key_subject, Scope.SUBJECT_READ)
        assert error.value.code == 1


@pytest.mark.parametrize(
    ("method", "path", "scope"),
    [
        ("GET", "/v1/memories/record", Scope.MEMORY_READ),
        ("GET", "/v1/entities/entity/graph", Scope.GRAPH_READ),
        ("DELETE", "/v1/memories/record", Scope.MEMORY_DELETE),
    ],
)
def test_sensitive_operations_are_gated_before_handler(
    tenants, issue_key, method, path, scope
):
    (workspace, agent, subject), (other_workspace, _, other_subject) = tenants
    right_scope = issue_key(workspace, agent, subject, scope)
    wrong_scope = issue_key(workspace, agent, subject, Scope.SUBJECT_READ)

    test_app = FastAPI()
    calls = []

    @test_app.api_route(path, methods=[method])
    def operation(access: Annotated[AgentAccess, Depends(require_subject(scope))]):
        calls.append(access)
        return {"agent_id": access.agent_id}

    with TestClient(test_app) as client:
        for target_workspace, target_subject, key in (
            (other_workspace, other_subject, right_scope),
            (workspace, other_subject, right_scope),
            (workspace, subject, wrong_scope),
        ):
            response = client.request(
                method,
                path,
                params={"workspace_id": target_workspace, "subject_id": target_subject},
                headers=bearer(key["api_key"]),
            )
            assert response.status_code == 403

        assert calls == []
        allowed = client.request(
            method,
            path,
            params={"workspace_id": workspace, "subject_id": subject},
            headers=bearer(right_scope["api_key"]),
        )
        assert allowed.status_code == 200
        assert allowed.json() == {"agent_id": agent}
