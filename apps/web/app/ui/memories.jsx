"use client";

import {
  Archive,
  BookOpen,
  Check,
  Clock3,
  Database,
  GitFork,
  Pencil,
  Search,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import {
  api,
  can,
  categories,
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
  Dialog,
  Empty,
  Field,
  Input,
  Notice,
  Panel,
  Select,
  Textarea,
  cn,
} from "./primitives";

function MemoryList({ items, selected, onSelect }) {
  if (!items.length)
    return <Empty icon={Archive} title="No matching memories" />;
  return items.map((item) => {
    const id = item.memory_id || item.id;
    return (
      <button
        className={cn(
          "activity-row memory-row",
          selected === id && "row-selected",
        )}
        key={id}
        onClick={() => onSelect(id)}
      >
        <span className="row-icon">
          <BookOpen size={16} />
        </span>
        <span className="activity-text">
          <strong>{item.content}</strong>
          <small>{timestamp(item.updated_at)}</small>
        </span>
        <Badge>{label(item.category)}</Badge>
      </button>
    );
  });
}

export function Memories({
  session,
  version,
  memoryId,
  openMemory,
  openEntity,
  refresh,
}) {
  const permitted = can(session, "memory:read");
  const [cursor, setCursor] = useState("");
  const [results, setResults] = useState(null);
  const action = useAction();
  const path = cursor
    ? "memories?after=" + encodeURIComponent(cursor)
    : "memories";
  const listing = useResource(session, permitted ? path : null, version);
  const detail = useResource(
    session,
    permitted && memoryId ? "memories/" + memoryId : null,
    version,
  );
  function filter(event) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const query = values.get("query").trim();
    const category = values.get("category");
    const status = values.get("status");
    if (!query && !category && !status) {
      setResults(null);
      return;
    }
    action.run(async () => {
      const data = {
        limit: 20,
        ...(query && { query }),
        ...(category && { category }),
        ...(status && { status }),
      };
      setResults(
        (await api(session, "memories/search", { method: "POST", data }))
          .evidence,
      );
    });
  }
  if (!permitted)
    return (
      <Panel>
        <Empty icon={Database} title="Memory access required">
          Use a key with memory:read.
        </Empty>
      </Panel>
    );
  return (
    <div className={cn("memories-grid", memoryId && "with-detail")}>
      <Panel title="Memory records">
        <form className="filter-bar" onSubmit={filter}>
          <span className="search-input">
            <Search size={16} />
            <Input
              name="query"
              aria-label="Search memories"
              placeholder="Search memories"
            />
          </span>
          <Select name="category" aria-label="Category">
            <option value="">All categories</option>
            {categories.map((item) => (
              <option key={item} value={item}>
                {label(item)}
              </option>
            ))}
          </Select>
          <Select name="status" aria-label="Memory state">
            <option value="">All states</option>
            {states.map((item) => (
              <option key={item} value={item}>
                {label(item)}
              </option>
            ))}
          </Select>
          <Button type="submit" variant="outline" disabled={action.pending}>
            Search
          </Button>
          {results && (
            <Button
              variant="ghost"
              type="reset"
              onClick={() => setResults(null)}
            >
              Clear
            </Button>
          )}
        </form>
        <Notice error={action.error || listing.error} />
        {listing.loading && !results && (
          <p className="panel-body secondary-text" role="status">
            Loading memories…
          </p>
        )}
        {(results || listing.data) && (
          <MemoryList
            items={results || listing.data.items}
            selected={memoryId}
            onSelect={openMemory}
          />
        )}
        {!results && (cursor || listing.data?.next_after) && (
          <div className="pagination">
            {cursor && (
              <Button variant="outline" onClick={() => setCursor("")}>
                First page
              </Button>
            )}
            {listing.data?.next_after && (
              <Button
                variant="outline"
                onClick={() => setCursor(listing.data.next_after)}
              >
                Next page
              </Button>
            )}
          </div>
        )}
      </Panel>
      {memoryId && (
        <Panel
          title="Memory detail"
          action={
            <Button variant="ghost" onClick={() => openMemory(null)}>
              Close
            </Button>
          }
        >
          <Notice error={detail.error} />
          {detail.loading && (
            <p className="panel-body secondary-text" role="status">
              Loading memory…
            </p>
          )}
          {detail.data && (
            <MemoryDetail
              key={memoryId}
              memory={detail.data}
              session={session}
              openEntity={openEntity}
              refresh={refresh}
              removed={() => {
                setResults(null);
                openMemory(null);
                refresh();
              }}
            />
          )}
        </Panel>
      )}
    </div>
  );
}

function Provenance({ source }) {
  return (
    <dl className="details-grid">
      <div>
        <dt>Source</dt>
        <dd className="break-anywhere">{source.source_ref}</dd>
      </div>
      <div>
        <dt>Origin</dt>
        <dd>{source.origin}</dd>
      </div>
      <div>
        <dt>Observed</dt>
        <dd>{timestamp(source.observed_at)}</dd>
      </div>
      <div>
        <dt>Trust class</dt>
        <dd>{label(source.trust_class)}</dd>
      </div>
    </dl>
  );
}

function ClaimCard({ claim, memory, session, openEntity, refresh }) {
  const [reviewing, setReviewing] = useState(false);
  const action = useAction();
  const mayReview = can(session, "memory:verify");
  const mayGraph = can(session, "graph:read");
  const entity = (item) =>
    item &&
    (mayGraph ? (
      <button
        type="button"
        className="text-link"
        onClick={() => openEntity(item.id)}
      >
        {item.reference}
      </button>
    ) : (
      item.reference
    ));
  function submit(event) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const outcome = values.get("outcome");
    const previous = values.get("supersedes")?.trim();
    const interval = values.get("interval")?.trim();
    const data = { claim_id: claim.id, outcome };
    if (outcome === "verified" && previous) data.supersedes_claim_id = previous;
    if (outcome === "verified" && interval)
      data.review_interval_days = Number(interval);
    action.run(async () => {
      await api(session, "memories/" + memory.id + "/verify", {
        method: "POST",
        data,
      });
      setReviewing(false);
      refresh();
    });
  }

  return (
    <article className="claim-card">
      <div className="claim-title">
        <strong>{label(claim.predicate)}</strong>
        <Badge tone={tone(claim.status)}>{label(claim.status)}</Badge>
      </div>
      <p className="claim-value">
        {entity(claim.subject_entity)} {claim.subject_entity && <span>·</span>}{" "}
        {claim.object_entity ? entity(claim.object_entity) : claim.object_value}
      </p>
      <dl className="details-grid">
        <div>
          <dt>Valid from</dt>
          <dd>{timestamp(claim.valid_from)}</dd>
        </div>
        <div>
          <dt>Valid to</dt>
          <dd>{timestamp(claim.valid_to)}</dd>
        </div>
        <div>
          <dt>Last verified</dt>
          <dd>{timestamp(claim.verified_at)}</dd>
        </div>
        <div>
          <dt>Review due</dt>
          <dd>{timestamp(claim.review_due_at)}</dd>
        </div>
      </dl>
      <details className="plain-details">
        <summary>Claim source</summary>
        <Provenance source={claim.source} />
        <p className="secondary-text id-text">{claim.id}</p>
      </details>
      {mayReview && (
        <Button variant="outline" onClick={() => setReviewing(true)}>
          <Check size={15} /> Review claim
        </Button>
      )}
      {reviewing && (
        <Dialog title="Review claim" onClose={() => setReviewing(false)}>
          <form className="form-grid" onSubmit={submit}>
            <Field label="Outcome">
              <Select name="outcome">
                <option value="verified">Verify</option>
                <option value="needs_review">Needs review</option>
                <option value="archived">Archive</option>
              </Select>
            </Field>
            <Field
              label="Supersedes claim ID"
              hint="Optional when verifying an effective-dated replacement."
            >
              <Input name="supersedes" placeholder="Previous claim UUID" />
            </Field>
            <Field
              label="Review interval in days"
              hint="Leave blank to use workspace defaults."
            >
              <Input name="interval" type="number" min="1" max="3650" />
            </Field>
            <Notice error={action.error} />
            <div className="form-actions">
              <Button type="submit" disabled={action.pending}>
                Save review
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </article>
  );
}

function MemoryDetail({ memory, session, openEntity, refresh, removed }) {
  const [mode, setMode] = useState(null);
  const [earlier, setEarlier] = useState([]);
  const history = memory.history.concat(
    earlier.flatMap((page) => page.history),
  );
  const before = earlier.length
    ? earlier.at(-1).next_revision_before
    : memory.next_revision_before;
  const action = useAction();
  return (
    <div className="panel-body stack">
      <div className="claim-title">
        <span className="flex flex-wrap gap-2">
          <Badge>{label(memory.category)}</Badge>
          {memory.state && (
            <Badge tone={tone(memory.state)}>{label(memory.state)}</Badge>
          )}
        </span>
        <span className="secondary-text">Revision {memory.revision}</span>
      </div>
      <p className="memory-content">{memory.content}</p>
      {memory.starts_at && (
        <div className="time-row">
          <Clock3 size={16} />{" "}
          <span>
            {timestamp(memory.starts_at, memory.timezone)} to{" "}
            {timestamp(memory.ends_at, memory.timezone)} · {memory.timezone}
          </span>
        </div>
      )}
      <Provenance source={memory.source} />
      <div className="button-row">
        {can(session, "memory:write") && (
          <Button variant="outline" onClick={() => setMode("edit")}>
            <Pencil size={15} /> Edit
          </Button>
        )}
        {can(session, "memory:delete") && (
          <Button variant="outline" onClick={() => setMode("delete")}>
            <Trash2 size={15} /> Delete
          </Button>
        )}
      </div>
      <section className="stack detail-section">
        <h3>Claims</h3>
        {memory.claims.length ? (
          memory.claims.map((claim) => (
            <ClaimCard
              key={claim.id}
              claim={claim}
              memory={memory}
              session={session}
              openEntity={openEntity}
              refresh={refresh}
            />
          ))
        ) : (
          <p className="secondary-text">No current claims.</p>
        )}
      </section>
      <section className="stack detail-section">
        <h3>Revision history</h3>
        {history.length ? (
          history.map((revision) => (
            <details className="plain-details" key={revision.revision}>
              <summary>
                Revision {revision.revision} · {timestamp(revision.updated_at)}
              </summary>
              <p className="memory-content">{revision.content}</p>
              <Provenance source={revision.source} />
              <ul className="revision-claims">
                {revision.claims.map((claim) => (
                  <li key={claim.id}>
                    {label(claim.predicate)} ·{" "}
                    {claim.object_value || claim.object_entity?.reference}{" "}
                    <Badge tone={tone(claim.status)}>
                      {label(claim.status)}
                    </Badge>
                  </li>
                ))}
              </ul>
            </details>
          ))
        ) : (
          <p className="secondary-text">This is the first revision.</p>
        )}
        {before && (
          <Button
            variant="outline"
            disabled={action.pending}
            onClick={() =>
              action.run(async () => {
                const older = await api(
                  session,
                  "memories/" + memory.id + "?revision_before=" + before,
                );
                setEarlier((previous) => [...previous, older]);
              })
            }
          >
            Load earlier revisions
          </Button>
        )}
      </section>
      <Notice error={action.error} />
      {mode === "edit" && (
        <MemoryEditor
          session={session}
          memory={memory}
          onClose={() => setMode(null)}
          onSaved={() => {
            setMode(null);
            refresh();
          }}
        />
      )}
      {mode === "delete" && (
        <Dialog title="Delete memory" onClose={() => setMode(null)}>
          <p>Delete this memory, its sources, and its revision history?</p>
          <div className="form-actions mt-5">
            <Button variant="outline" onClick={() => setMode(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={action.pending}
              onClick={() =>
                action.run(async () => {
                  await api(session, "memories/" + memory.id, {
                    method: "DELETE",
                  });
                  removed();
                })
              }
            >
              Delete memory
            </Button>
          </div>
          <Notice error={action.error} />
        </Dialog>
      )}
    </div>
  );
}

export function MemoryEditor({ session, memory, onClose, onSaved }) {
  const [claim, setClaim] = useState(false);
  const action = useAction();
  function submit(event) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const read = (name) => values.get(name)?.trim() || "";
    const source = {
      origin: read("origin"),
      source_ref: read("source"),
      observed_at: read("observed") || new Date().toISOString(),
      trust_class: read("trust"),
    };
    const data = {
      content: read("content"),
      category: read("category"),
      source,
    };
    const timing = {
      starts_at: read("starts") || null,
      ends_at: read("ends") || null,
      timezone: read("timezone") || null,
      state: read("state") || null,
    };
    if (memory?.starts_at || Object.values(timing).some(Boolean))
      Object.assign(data, timing);
    if (claim) {
      const subject = read("subject-name");
      const candidate = { predicate: read("predicate") };
      if (subject)
        candidate.subject_entity = {
          kind: read("subject-kind"),
          reference: subject,
        };
      if (read("target-kind"))
        candidate.object_entity = {
          kind: read("target-kind"),
          reference: read("object"),
        };
      else candidate.object_value = read("object");
      if (read("valid-from")) candidate.valid_from = read("valid-from");
      data.claims = [candidate];
    }
    action.run(async () => {
      const item = await api(
        session,
        memory ? "memories/" + memory.id : "memories",
        {
          method: memory ? "PATCH" : "POST",
          data,
        },
      );
      onSaved(item.id);
    });
  }
  return (
    <Dialog title={memory ? "Edit memory" : "New memory"} onClose={onClose}>
      <form className="form-grid" onSubmit={submit}>
        <Field label="Content">
          <Textarea
            name="content"
            required
            maxLength={20000}
            defaultValue={memory?.content}
          />
        </Field>
        <Field label="Category">
          <Select name="category" defaultValue={memory?.category || "semantic"}>
            {categories.map((item) => (
              <option key={item} value={item}>
                {label(item)}
              </option>
            ))}
          </Select>
        </Field>
        <div className="form-columns">
          <Field label="Source origin">
            <Input
              name="origin"
              required
              defaultValue={memory?.source.origin || "console"}
            />
          </Field>
          <Field label="Source reference">
            <Input
              name="source"
              required
              defaultValue={memory?.source.source_ref}
              placeholder="e.g. note:123"
            />
          </Field>
        </div>
        <Field label="Observed at" hint="Leave blank to use the current time.">
          <Input
            name="observed"
            defaultValue={memory?.source.observed_at}
            placeholder="ISO 8601 with offset"
          />
        </Field>
        <Field label="Source trust">
          <Select
            name="trust"
            defaultValue={memory?.source.trust_class || "unverified"}
          >
            {[
              "unverified",
              "agent_reported",
              "user_confirmed",
              "authoritative",
            ].map((item) => (
              <option key={item} value={item}>
                {label(item)}
              </option>
            ))}
          </Select>
        </Field>
        <details className="plain-details">
          <summary>Time window</summary>
          <div className="form-grid mt-3">
            <div className="form-columns">
              <Field label="Starts at">
                <Input
                  name="starts"
                  defaultValue={memory?.starts_at}
                  placeholder="ISO 8601 with offset"
                />
              </Field>
              <Field label="Ends at">
                <Input
                  name="ends"
                  defaultValue={memory?.ends_at}
                  placeholder="ISO 8601 with offset"
                />
              </Field>
            </div>
            <div className="form-columns">
              <Field label="Timezone">
                <Input
                  name="timezone"
                  defaultValue={memory?.timezone}
                  placeholder="America/New_York"
                />
              </Field>
              <Field label="State">
                <Select name="state" defaultValue={memory?.state || ""}>
                  <option value="">Unspecified</option>
                  {states.map((item) => (
                    <option key={item} value={item}>
                      {label(item)}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          </div>
        </details>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={claim}
            onChange={(event) => setClaim(event.target.checked)}
          />{" "}
          Submit a candidate claim
        </label>
        {claim && (
          <div className="form-grid claim-card">
            <Field label="Predicate">
              <Input name="predicate" placeholder="e.g. works_for" required />
            </Field>
            <div className="form-columns">
              <Field label="Subject entity">
                <Input name="subject-name" placeholder="Optional reference" />
              </Field>
              <Field label="Subject kind">
                <Input name="subject-kind" defaultValue="person" />
              </Field>
            </div>
            <div className="form-columns">
              <Field label="Object value or reference">
                <Input name="object" required />
              </Field>
              <Field
                label="Object entity kind"
                hint="Set a kind to create a graph edge."
              >
                <Input name="target-kind" placeholder="Optional" />
              </Field>
            </div>
            <Field label="Claim valid from">
              <Input name="valid-from" placeholder="ISO 8601 with offset" />
            </Field>
          </div>
        )}
        <Notice error={action.error} />
        <div className="form-actions">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={action.pending}>
            Save memory
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
