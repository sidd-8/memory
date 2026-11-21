"use client";

import { Bot, Clipboard, KeyRound, Plus, Settings2 } from "lucide-react";
import { useState } from "react";
import {
  api,
  can,
  scopes,
  shortId,
  timestamp,
  useAction,
  useResource,
} from "./data";
import {
  Badge,
  Button,
  Dialog,
  Empty,
  Field,
  Input,
  Notice,
  Panel,
  Select,
} from "./primitives";

export function Agents({ session }) {
  const permitted = can(session, "workspace:manage");
  const [version, setVersion] = useState(0);
  const [creating, setCreating] = useState(false);
  const agents = useResource(
    session,
    permitted ? "console/agents" : null,
    version,
  );
  const keys = useResource(session, permitted ? "console/keys" : null, version);
  const action = useAction();
  if (!permitted)
    return (
      <Panel>
        <Empty icon={Bot} title="Agent management requires workspace access">
          Connect an operator key with workspace:manage.
        </Empty>
      </Panel>
    );

  function create(event) {
    event.preventDefault();
    const name = new FormData(event.currentTarget).get("name").trim();
    action.run(async () => {
      await api(session, "console/agents", { method: "POST", data: { name } });
      setCreating(false);
      setVersion((previous) => previous + 1);
    });
  }
  return (
    <Panel
      title="Registered agents"
      action={
        <Button onClick={() => setCreating(true)}>
          <Plus size={15} /> Create agent
        </Button>
      }
    >
      <Notice error={agents.error || keys.error} />
      {agents.loading && (
        <p role="status" className="panel-body secondary-text">
          Loading agents…
        </p>
      )}
      {agents.data && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>Scopes for this subject</th>
                <th>Last activity</th>
                <th>Active keys</th>
              </tr>
            </thead>
            <tbody>
              {agents.data.map((agent) => {
                const active = (keys.data || []).filter(
                  (key) => key.agent_id === agent.id && !key.revoked_at,
                );
                const granted = [
                  ...new Set(active.flatMap((key) => key.scopes)),
                ].sort();
                return (
                  <tr key={agent.id}>
                    <td>
                      <div className="table-name">
                        <span className="row-icon">
                          <Bot size={16} />
                        </span>
                        <div>
                          <strong>{agent.name}</strong>
                          <small>{shortId(agent.id)}</small>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="badge-row">
                        {granted.length
                          ? granted.map((scope) => (
                              <Badge key={scope}>{scope}</Badge>
                            ))
                          : "—"}
                      </div>
                    </td>
                    <td>{timestamp(agent.last_activity)}</td>
                    <td>{active.length}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {creating && (
        <Dialog title="Create agent" onClose={() => setCreating(false)}>
          <form className="form-grid" onSubmit={create}>
            <Field label="Agent name">
              <Input name="name" required maxLength={128} />
            </Field>
            <Notice error={action.error} />
            <div className="form-actions">
              <Button type="submit" disabled={action.pending}>
                Create agent
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </Panel>
  );
}

function KeySettings({ session }) {
  const [version, setVersion] = useState(0);
  const [modal, setModal] = useState(null);
  const [issued, setIssued] = useState(null);
  const [copied, setCopied] = useState(false);
  const keys = useResource(session, "console/keys", version);
  const agents = useResource(session, "console/agents", version);
  const action = useAction();

  function issue(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const scopes = form.getAll("scope");
    action.run(async () => {
      if (!scopes.length) throw new Error("Select at least one scope.");
      const result = await api(session, "console/keys", {
        method: "POST",
        data: { agent_id: form.get("agent"), scopes },
      });
      setIssued(result);
      setCopied(false);
      setModal("issued");
      setVersion((previous) => previous + 1);
    });
  }

  return (
    <Panel
      title="API keys"
      action={
        <Button variant="outline" onClick={() => setModal("issue")}>
          <Plus size={15} /> Issue key
        </Button>
      }
    >
      <Notice error={keys.error || agents.error} />
      {keys.loading && (
        <p className="panel-body secondary-text" role="status">
          Loading keys…
        </p>
      )}
      {keys.data && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Key</th>
                <th>Scopes</th>
                <th>Status</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {keys.data.map((key) => (
                <tr key={key.id}>
                  <td>
                    <strong>{key.agent}</strong>
                    <div className="secondary-text id-text">
                      {shortId(key.id)}
                    </div>
                  </td>
                  <td>
                    <div className="badge-row">
                      {key.scopes.map((scope) => (
                        <Badge key={scope}>{scope}</Badge>
                      ))}
                    </div>
                  </td>
                  <td>
                    <Badge tone={key.revoked_at ? "neutral" : "green"}>
                      {key.revoked_at ? "Revoked" : "Active"}
                    </Badge>
                  </td>
                  <td>
                    {!key.revoked_at &&
                      !key.scopes.includes("workspace:manage") && (
                        <Button variant="ghost" onClick={() => setModal(key)}>
                          Revoke
                        </Button>
                      )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {modal === "issue" && (
        <Dialog title="Issue a subject API key" onClose={() => setModal(null)}>
          <form className="form-grid" onSubmit={issue}>
            <Field label="Agent">
              <Select name="agent" required>
                <option value="">Choose an agent</option>
                {agents.data?.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </Select>
            </Field>
            <fieldset className="scope-list">
              <legend>Scopes</legend>
              {scopes.map((scope) => (
                <label className="checkbox-label" key={scope}>
                  <input name="scope" type="checkbox" value={scope} /> {scope}
                </label>
              ))}
            </fieldset>
            <Notice error={action.error} />
            <div className="form-actions">
              <Button type="submit" disabled={action.pending}>
                Issue key
              </Button>
            </div>
          </form>
        </Dialog>
      )}
      {modal === "issued" && (
        <Dialog
          title="Save your API key"
          onClose={() => {
            setModal(null);
            setIssued(null);
          }}
        >
          <div className="stack">
            <p>Copy the key now. The server stores only its hash.</p>
            <Field label="New API key">
              <Input readOnly value={issued.api_key} className="id-text" />
            </Field>
            <Notice error={action.error} />
            <div className="form-actions">
              <Button
                variant="outline"
                onClick={() =>
                  action.run(async () => {
                    await navigator.clipboard.writeText(issued.api_key);
                    setCopied(true);
                  })
                }
              >
                <Clipboard size={15} />
                {copied ? "Copied" : "Copy key"}
              </Button>
              <Button
                onClick={() => {
                  setModal(null);
                  setIssued(null);
                }}
              >
                Done
              </Button>
            </div>
          </div>
        </Dialog>
      )}
      {modal && typeof modal === "object" && (
        <Dialog title="Revoke API key" onClose={() => setModal(null)}>
          <p>
            Revoke this key for {modal.agent}? Requests using it will be denied.
          </p>
          <Notice error={action.error} />
          <div className="form-actions mt-5">
            <Button variant="outline" onClick={() => setModal(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={action.pending}
              onClick={() =>
                action.run(async () => {
                  await api(session, "console/keys/" + modal.id, {
                    method: "DELETE",
                  });
                  setModal(null);
                  setVersion((previous) => previous + 1);
                })
              }
            >
              Revoke key
            </Button>
          </div>
        </Dialog>
      )}
    </Panel>
  );
}

export function WorkspaceSettings({
  session,
  onDisconnect,
  onUpdate,
  connection,
}) {
  const action = useAction();
  const [saved, setSaved] = useState(false);
  if (!session)
    return (
      <Panel title="Workspace access" className="settings-panel">
        <div className="panel-body">{connection}</div>
      </Panel>
    );
  const permitted = can(session, "workspace:manage");
  function save(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const value = (name) => (form.get(name) ? Number(form.get(name)) : null);
    setSaved(false);
    action.run(async () => {
      const settings = await api(session, "console/settings", {
        method: "PATCH",
        data: {
          name: form.get("name").trim(),
          default_retention_days: value("retention"),
          default_review_days: value("review"),
        },
      });
      onUpdate({ ...session, ...settings });
      setSaved(true);
    });
  }
  return (
    <div className="stack">
      <div className="overview-grid">
        <Panel
          title="Workspace defaults"
          action={<Settings2 size={16} className="text-subtle" />}
        >
          <form className="panel-body form-grid" onSubmit={save}>
            <Field label="Workspace name">
              <Input
                name="name"
                defaultValue={session.workspace}
                required
                maxLength={128}
                readOnly={!permitted}
              />
            </Field>
            <Field
              label="Default retention in days"
              hint="Applies to new memories with no explicit expiration."
            >
              <Input
                name="retention"
                type="number"
                min="1"
                max="3650"
                placeholder="No default expiration"
                defaultValue={session.default_retention_days}
                readOnly={!permitted}
              />
            </Field>
            <Field
              label="Default review interval in days"
              hint="Leave blank to use the claim and category defaults."
            >
              <Input
                name="review"
                type="number"
                min="1"
                max="3650"
                placeholder="Use claim defaults"
                defaultValue={session.default_review_days}
                readOnly={!permitted}
              />
            </Field>
            <Notice error={action.error} />
            {permitted && (
              <div className="form-actions">
                {saved && (
                  <span className="secondary-text" role="status">
                    Settings saved.
                  </span>
                )}
                <Button type="submit" disabled={action.pending}>
                  Save settings
                </Button>
              </div>
            )}
          </form>
        </Panel>
        <Panel
          title="Current connection"
          action={<KeyRound size={16} className="text-subtle" />}
        >
          <div className="panel-body stack">
            <dl className="details-grid one-column">
              <div>
                <dt>Agent</dt>
                <dd>{session.agent}</dd>
              </div>
              <div>
                <dt>Workspace ID</dt>
                <dd className="id-text">{session.workspaceId}</dd>
              </div>
              <div>
                <dt>Subject ID</dt>
                <dd className="id-text">{session.subjectId}</dd>
              </div>
            </dl>
            <div className="badge-row">
              {session.scopes.map((scope) => (
                <Badge key={scope}>{scope}</Badge>
              ))}
            </div>
            <Button variant="outline" onClick={onDisconnect}>
              Disconnect
            </Button>
          </div>
        </Panel>
      </div>
      {permitted && <KeySettings session={session} />}
    </div>
  );
}
