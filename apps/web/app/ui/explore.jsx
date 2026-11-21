"use client";

import { Database, GitFork, Network, Play, Terminal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  api,
  can,
  label,
  states,
  timestamp,
  tone,
  useAction,
  useResource,
} from "./data";
import {
  Badge,
  Button,
  Empty,
  Field,
  Input,
  Notice,
  Panel,
  Select,
  Textarea,
  cn,
} from "./primitives";

export function Playground({ session, openMemory }) {
  const [result, setResult] = useState(null);
  const action = useAction();
  if (!can(session, "memory:read"))
    return (
      <Panel>
        <Empty icon={Terminal} title="Retrieval requires memory access">
          Use an agent key with memory:read.
        </Empty>
      </Panel>
    );

  function run(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const query = form.get("query").trim();
    const status = form.get("status");
    const starts = form.get("starts").trim();
    const ends = form.get("ends").trim();
    action.run(async () => {
      if (Boolean(starts) !== Boolean(ends))
        throw new Error("Supply both timestamps to filter by a time window.");
      const data = {
        ...(query && { query }),
        ...(status && { status }),
        ...(starts && { time_range: { starts_at: starts, ends_at: ends } }),
      };
      setResult(
        await api(session, "memories/search", { method: "POST", data }),
      );
    });
  }
  return (
    <div className="overview-grid">
      <Panel
        title="Retrieved evidence"
        action={result && <Badge>{label(result.mode)}</Badge>}
      >
        {!result ? (
          <Empty icon={Terminal} title="Run a retrieval query">
            Evidence and source IDs will appear here.
          </Empty>
        ) : result.evidence.length === 0 ? (
          <Empty icon={Database} title="No matching evidence" />
        ) : (
          <div className="panel-body stack">
            {result.evidence.map((hit) => (
              <article key={hit.memory_id} className="claim-card">
                <div className="claim-title">
                  <Badge>{label(hit.category)}</Badge>
                  {hit.state && (
                    <Badge tone={tone(hit.state)}>{label(hit.state)}</Badge>
                  )}
                </div>
                <p className="memory-content">{hit.content}</p>
                <dl className="details-grid one-column">
                  <div>
                    <dt>Source ID</dt>
                    <dd className="id-text">{hit.source_id}</dd>
                  </div>
                  {hit.starts_at && (
                    <div>
                      <dt>Time window</dt>
                      <dd>
                        {timestamp(hit.starts_at, hit.timezone)} to{" "}
                        {timestamp(hit.ends_at, hit.timezone)} · {hit.timezone}
                      </dd>
                    </div>
                  )}
                </dl>
                {hit.claims.map((claim) => (
                  <div className="claim-title" key={claim.id}>
                    <span>
                      {label(claim.predicate)} · {claim.value}
                    </span>
                    <Badge tone={tone(claim.status)}>
                      {label(claim.status)}
                    </Badge>
                  </div>
                ))}
                <Button
                  variant="outline"
                  onClick={() => openMemory(hit.memory_id)}
                >
                  Inspect memory
                </Button>
              </article>
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Run retrieval">
        <form onSubmit={run} className="panel-body form-grid">
          <dl className="details-grid">
            <div>
              <dt>Agent</dt>
              <dd>{session.agent}</dd>
            </div>
            <div>
              <dt>Subject</dt>
              <dd>{session.subject}</dd>
            </div>
          </dl>
          <Field label="Query">
            <Textarea
              name="query"
              placeholder="Which service stores inventory records?"
            />
          </Field>
          <details className="plain-details">
            <summary>Time and state filters</summary>
            <div className="form-grid mt-3">
              <Field label="Window starts">
                <Input name="starts" placeholder="ISO 8601 with offset" />
              </Field>
              <Field label="Window ends">
                <Input name="ends" placeholder="ISO 8601 with offset" />
              </Field>
              <Field label="State">
                <Select name="status">
                  <option value="">All states</option>
                  {states.map((item) => (
                    <option key={item} value={item}>
                      {label(item)}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          </details>
          <Notice error={action.error} />
          <Button type="submit" disabled={action.pending}>
            <Play size={15} />
            {action.pending ? "Retrieving…" : "Run retrieval"}
          </Button>
        </form>
      </Panel>
    </div>
  );
}

function GraphCanvas({ data, selected, onSelect }) {
  const container = useRef(null);
  const canvas = useRef(null);
  const direct = new Set(
    data.edges
      .filter((edge) =>
        [edge.from_entity_id, edge.to_entity_id].includes(data.root_id),
      )
      .flatMap((edge) => [edge.from_entity_id, edge.to_entity_id]),
  );
  const groups = [
    data.nodes.filter((node) => node.id === data.root_id),
    data.nodes.filter(
      (node) => node.id !== data.root_id && direct.has(node.id),
    ),
    data.nodes.filter(
      (node) => node.id !== data.root_id && !direct.has(node.id),
    ),
  ].filter((group) => group.length);
  const height = Math.max(
    320,
    ...groups.map((group) => group.length * 55 + 44),
  );
  const coordinates = new Map(
    groups.flatMap((group, column) =>
      group.map((node, row) => [
        node.id,
        [(column + 0.5) / groups.length, (row + 1) / (group.length + 1)],
      ]),
    ),
  );

  useEffect(() => {
    const element = container.current;
    const target = canvas.current;
    const draw = () => {
      const { width, height } = element.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      target.width = Math.round(width * scale);
      target.height = Math.round(height * scale);
      const context = target.getContext("2d");
      if (!context) return;
      context.scale(scale, scale);
      for (const edge of data.edges) {
        const from = coordinates.get(edge.from_entity_id);
        const to = coordinates.get(edge.to_entity_id);
        if (!from || !to) continue;
        context.beginPath();
        context.strokeStyle = [edge.from_entity_id, edge.to_entity_id].includes(
          selected,
        )
          ? "#7777d0"
          : "#c4c9dc";
        context.lineWidth = 1.4;
        context.moveTo(from[0] * width, from[1] * height);
        context.lineTo(to[0] * width, to[1] * height);
        context.stroke();
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(element);
    return () => observer.disconnect();
  });

  return (
    <div className="graph-canvas" ref={container} style={{ height }}>
      <canvas ref={canvas} aria-hidden="true" />
      {data.nodes.map((node) => {
        const [x, y] = coordinates.get(node.id);
        return (
          <button
            key={node.id}
            type="button"
            aria-pressed={selected === node.id}
            title={node.reference}
            onClick={() => onSelect(node.id)}
            className={cn(
              "graph-node",
              selected === node.id && "graph-selected",
            )}
            style={{ left: x * 100 + "%", top: y * 100 + "%" }}
          >
            <Network size={14} aria-hidden="true" />
            <span>
              {node.reference}
              <small>{label(node.kind)}</small>
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function Graph({ session, entityId, openEntity, openMemory }) {
  const [depth, setDepth] = useState(1);
  const [selected, setSelected] = useState(null);
  const path = entityId
    ? "entities/" +
      encodeURIComponent(entityId) +
      "/graph?depth=" +
      depth +
      "&edge_limit=12"
    : null;
  const permitted = can(session, "graph:read");
  const { data, error, loading } = useResource(
    session,
    permitted ? path : null,
  );
  if (!permitted)
    return (
      <Panel>
        <Empty icon={GitFork} title="Graph access required">
          Use a key with graph:read.
        </Empty>
      </Panel>
    );

  function load(event) {
    event.preventDefault();
    const id = new FormData(event.currentTarget).get("root").trim();
    setDepth(1);
    setSelected(null);
    openEntity(id);
  }
  const selectedId = data?.nodes.some((node) => node.id === selected)
    ? selected
    : data?.root_id;
  const node = data?.nodes.find((item) => item.id === selectedId);
  const edges = (data?.edges || []).filter((edge) =>
    [edge.from_entity_id, edge.to_entity_id].includes(selectedId),
  );

  return (
    <div className="stack">
      <Panel>
        <form className="filter-bar" onSubmit={load}>
          <Input
            key={entityId}
            name="root"
            defaultValue={entityId || ""}
            aria-label="Entity ID"
            placeholder="Entity UUID"
            required
          />
          <Button type="submit">Open entity</Button>
          {entityId && (
            <Button
              variant="outline"
              onClick={() => setDepth(depth === 1 ? 2 : 1)}
            >
              {depth === 1 ? "Expand to two hops" : "Show direct connections"}
            </Button>
          )}
        </form>
      </Panel>
      <Notice error={error} />
      {loading && (
        <p className="secondary-text" role="status">
          Loading relationships…
        </p>
      )}
      {!entityId && (
        <Panel>
          <Empty icon={GitFork} title="Select an entity">
            Open an entity by ID or select one from a memory claim.
          </Empty>
        </Panel>
      )}
      {data && (
        <div className="overview-grid">
          <Panel
            title="Neighborhood"
            action={<Badge>{depth === 1 ? "One hop" : "Two hops"}</Badge>}
          >
            <GraphCanvas
              data={data}
              selected={selectedId}
              onSelect={setSelected}
            />
          </Panel>
          <Panel title={node?.reference || "Relationships"}>
            <div className="panel-body stack">
              {edges.length ? (
                edges.map((edge) => {
                  const neighborId =
                    edge.from_entity_id === selectedId
                      ? edge.to_entity_id
                      : edge.from_entity_id;
                  const neighbor = data.nodes.find(
                    (item) => item.id === neighborId,
                  );
                  return (
                    <article className="claim-card" key={edge.claim_id}>
                      <div className="claim-title">
                        <strong>{label(edge.predicate)}</strong>
                        <Badge tone={tone(edge.status)}>
                          {label(edge.status)}
                        </Badge>
                      </div>
                      <button
                        type="button"
                        className="text-link"
                        onClick={() => setSelected(neighborId)}
                      >
                        {neighbor?.reference}
                      </button>
                      <dl className="details-grid one-column">
                        <div>
                          <dt>Source ID</dt>
                          <dd className="id-text">{edge.source_id}</dd>
                        </div>
                        <div>
                          <dt>Validity</dt>
                          <dd>
                            {timestamp(edge.valid_from)} to{" "}
                            {timestamp(edge.valid_to)}
                          </dd>
                        </div>
                      </dl>
                      {can(session, "memory:read") && (
                        <Button
                          variant="outline"
                          onClick={() => openMemory(edge.memory_id)}
                        >
                          Inspect memory
                        </Button>
                      )}
                    </article>
                  );
                })
              ) : (
                <p className="secondary-text">No relationships in this view.</p>
              )}
              {selectedId !== entityId && (
                <Button
                  variant="outline"
                  onClick={() => {
                    setDepth(1);
                    setSelected(null);
                    openEntity(selectedId);
                  }}
                >
                  Center on entity
                </Button>
              )}
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
