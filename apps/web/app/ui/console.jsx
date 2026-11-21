"use client";

import {
  Activity,
  Archive,
  ArrowDownToLine,
  BookOpen,
  Bot,
  ChevronDown,
  CircleHelp,
  Command,
  Database,
  GitFork,
  KeyRound,
  Layers3,
  Plus,
  Settings,
  Terminal,
} from "lucide-react";
import { useState } from "react";
import { Agents, WorkspaceSettings } from "./admin";
import { api, can, label, timestamp, useAction, useResource } from "./data";
import { Graph, Playground } from "./explore";
import { Memories, MemoryEditor } from "./memories";
import {
  Badge,
  Button,
  Empty,
  Field,
  Input,
  Notice,
  Panel,
  cn,
} from "./primitives";

const sections = [
  {
    name: "Overview",
    icon: Activity,
    description: "Recent activity and claims that need attention.",
  },
  {
    name: "Agents",
    icon: Bot,
    description: "Register agents and inspect access to the current subject.",
  },
  {
    name: "Memories",
    icon: Database,
    description: "Browse saved context, inspect sources, and correct claims.",
  },
  {
    name: "Graph",
    icon: GitFork,
    description: "Explore the relationships around a selected entity.",
  },
  {
    name: "Playground",
    icon: Terminal,
    description:
      "Run retrieval as the connected agent and inspect the evidence.",
  },
  {
    name: "Settings",
    icon: Settings,
    description: "Manage workspace defaults, retention, and API keys.",
  },
];

function Connection({ onConnect, onCancel }) {
  const action = useAction();
  function submit(event) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const values = {
      workspaceId: fields.get("workspace").trim(),
      subjectId: fields.get("subject").trim(),
      key: fields.get("key").trim(),
    };
    action.run(async () =>
      onConnect({ ...values, ...(await api(values, "console/session")) }),
    );
  }

  return (
    <form onSubmit={submit} className="form-grid">
      <Field label="Workspace ID">
        <Input
          name="workspace"
          placeholder="Workspace UUID"
          required
          autoComplete="off"
        />
      </Field>
      <Field label="Subject ID">
        <Input
          name="subject"
          placeholder="Subject UUID"
          required
          autoComplete="off"
        />
      </Field>
      <Field label="API key">
        <Input
          name="key"
          placeholder="am_…"
          type="password"
          required
          autoComplete="off"
        />
      </Field>
      <Notice error={action.error} />
      <div className="form-actions">
        {onCancel && (
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button type="submit" disabled={action.pending}>
          <KeyRound size={15} />
          {action.pending ? "Connecting…" : "Connect workspace"}
        </Button>
      </div>
    </form>
  );
}

function Overview({ session, openMemory, version, onConnect }) {
  const access = can(session, "memory:read");
  const { data, error, loading } = useResource(
    session,
    access ? "console/overview" : null,
    version,
  );
  if (!session)
    return (
      <div className="overview-grid">
        <Panel className="welcome-panel">
          <div className="welcome-icon">
            <Layers3 size={25} strokeWidth={1.6} />
          </div>
          <h2>Connect a workspace</h2>
          <p>
            Use a scoped API key to view memories, activity, and claims for one
            subject.
          </p>
          <div className="welcome-features">
            <span>
              <Database size={16} /> Inspect source-backed memories
            </span>
            <span>
              <GitFork size={16} /> Explore related entities
            </span>
            <span>
              <Terminal size={16} /> Test retrieval as an agent
            </span>
          </div>
        </Panel>
        <Panel title="Workspace access">
          <div className="panel-body">
            <Connection onConnect={onConnect} />
          </div>
        </Panel>
      </div>
    );
  if (!access)
    return (
      <Panel>
        <Empty icon={KeyRound} title="Memory access required">
          Connect an API key with memory:read to view activity.
        </Empty>
      </Panel>
    );

  return (
    <>
      <Notice error={error} />
      {loading && (
        <p role="status" className="secondary-text">
          Loading activity…
        </p>
      )}
      {data && (
        <div className="overview-grid">
          <div className="stack">
            <Panel
              title="Recent writes"
              action={<Database size={16} className="text-subtle" />}
            >
              {data.writes.length ? (
                data.writes.map((item) => (
                  <button
                    className="activity-row"
                    key={item.memory_id}
                    onClick={() => openMemory(item.memory_id)}
                  >
                    <span className="row-icon">
                      <BookOpen size={16} />
                    </span>
                    <span className="activity-text">
                      <strong>{item.content}</strong>
                      <small>{timestamp(item.recorded_at)}</small>
                    </span>
                    <Badge>{label(item.category)}</Badge>
                  </button>
                ))
              ) : (
                <Empty icon={Archive} title="No memories written yet" />
              )}
            </Panel>
            <Panel
              title="Recent retrievals"
              action={<ArrowDownToLine size={16} className="text-subtle" />}
            >
              {data.retrievals.length ? (
                data.retrievals.map((item, index) => (
                  <div className="activity-row" key={item.recorded_at + index}>
                    <span className="row-icon">
                      <Terminal size={16} />
                    </span>
                    <span className="activity-text">
                      <strong>
                        {item.agent} retrieved {item.result_count}{" "}
                        {item.result_count === 1 ? "memory" : "memories"}
                      </strong>
                      <small>{timestamp(item.recorded_at)}</small>
                    </span>
                    {item.memory_id && (
                      <Button
                        variant="ghost"
                        onClick={() => openMemory(item.memory_id)}
                      >
                        View
                      </Button>
                    )}
                  </div>
                ))
              ) : (
                <Empty icon={Terminal} title="No retrievals yet" />
              )}
            </Panel>
          </div>
          <Panel
            title="Review queue"
            action={<Activity size={16} className="text-subtle" />}
          >
            {data.reviews.length ? (
              data.reviews.map((item) => (
                <button
                  className="activity-row"
                  key={item.claim_id}
                  onClick={() => openMemory(item.memory_id)}
                >
                  <span className="activity-text">
                    <strong>{label(item.predicate)}</strong>
                    <small>{item.content}</small>
                  </span>
                  <Badge tone="amber">{label(item.status)}</Badge>
                </button>
              ))
            ) : (
              <Empty icon={CircleHelp} title="No claims awaiting review" />
            )}
          </Panel>
        </div>
      )}
    </>
  );
}

export default function Console() {
  const [tab, setTab] = useState("Overview");
  const [session, setSession] = useState(null);
  const [epoch, setEpoch] = useState(0);
  const [version, setVersion] = useState(0);
  const [memoryId, setMemoryId] = useState(null);
  const [entityId, setEntityId] = useState(null);
  const [editing, setEditing] = useState(false);

  const section = sections.find((item) => item.name === tab);
  const refresh = () => setVersion((previous) => previous + 1);
  const openMemory = (id) => {
    setMemoryId(id);
    setTab("Memories");
  };
  const openEntity = (id) => {
    setEntityId(id);
    setTab("Graph");
  };
  const connect = (next) => {
    setSession(next);
    setEpoch((previous) => previous + 1);
    setMemoryId(null);
    setEntityId(null);
    setTab("Overview");
  };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Layers3 size={19} strokeWidth={2.2} />
          </span>
          <strong>Agent Memory</strong>
        </div>
        <button
          className="workspace-button"
          onClick={() => setTab("Settings")}
          aria-label="Workspace settings"
        >
          <span className="workspace-avatar">
            {session?.workspace[0].toUpperCase() || <Command size={16} />}
          </span>
          <span className="workspace-name">
            <strong>{session?.workspace || "Local workspace"}</strong>
            <small>{session?.subject || "Connect a subject"}</small>
          </span>
          <ChevronDown size={14} className="text-subtle" />
        </button>
        <nav aria-label="Console">
          {sections.map(({ name, icon: Icon }) => (
            <button
              key={name}
              type="button"
              onClick={() => setTab(name)}
              className={cn("nav-item", tab === name && "nav-selected")}
              aria-current={tab === name ? "page" : undefined}
            >
              <Icon size={17} strokeWidth={1.8} aria-hidden="true" />
              <span>{name}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className={cn("connection-dot", session && "is-connected")} />
          {session ? "Workspace connected" : "Local environment"}
        </div>
      </aside>

      <div className="app-content">
        <div className="topbar">
          <div className="breadcrumbs">
            <span>{session?.workspace || "Workspace"}</span>
            <span className="slash">/</span>
            <strong>{tab}</strong>
          </div>
          <Button
            variant="outline"
            className="top-action"
            onClick={() => setTab("Settings")}
          >
            <KeyRound size={14} />
            {session ? session.agent : "Connect"}
          </Button>
        </div>
        <main id="main" className="main-content" tabIndex={-1}>
          <header className="page-heading">
            <div>
              <h1>{tab}</h1>
              <p>{section.description}</p>
            </div>
            {can(session, "memory:write") &&
              ["Overview", "Memories"].includes(tab) && (
                <Button onClick={() => setEditing(true)}>
                  <Plus size={16} /> New memory
                </Button>
              )}
          </header>

          <div key={epoch + ":" + tab}>
            {tab === "Overview" && (
              <Overview
                session={session}
                openMemory={openMemory}
                version={version}
                onConnect={connect}
              />
            )}
            {tab === "Settings" && (
              <WorkspaceSettings
                session={session}
                onDisconnect={() => connect(null)}
                onUpdate={setSession}
                connection={<Connection onConnect={connect} />}
              />
            )}
            {!session && !["Overview", "Settings"].includes(tab) && (
              <Panel>
                <Empty icon={KeyRound} title="Connect a workspace to continue">
                  <Button variant="outline" onClick={() => setTab("Settings")}>
                    Workspace access
                  </Button>
                </Empty>
              </Panel>
            )}
            {session && tab === "Agents" && <Agents session={session} />}
            {session && tab === "Memories" && (
              <Memories
                session={session}
                version={version}
                memoryId={memoryId}
                openMemory={setMemoryId}
                openEntity={openEntity}
                refresh={refresh}
              />
            )}
            {session && tab === "Graph" && (
              <Graph
                session={session}
                entityId={entityId}
                openEntity={setEntityId}
                openMemory={openMemory}
              />
            )}
            {session && tab === "Playground" && (
              <Playground session={session} openMemory={openMemory} />
            )}
          </div>
        </main>
      </div>
      {editing && session && (
        <MemoryEditor
          session={session}
          onClose={() => setEditing(false)}
          onSaved={(id) => {
            setEditing(false);
            refresh();
            openMemory(id);
          }}
        />
      )}
    </div>
  );
}
