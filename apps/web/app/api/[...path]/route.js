const namespaces = new Set(["console", "entities", "memories"]);

async function proxy(request, { params }) {
  const { path } = await params;
  if (
    path[0] !== "v1" ||
    !namespaces.has(path[1]) ||
    path.some((part) => !/^[a-z0-9-]+$/i.test(part))
  ) {
    return Response.json({ detail: "Unknown API route" }, { status: 404 });
  }

  const upstream = new URL(
    "/" + path.map(encodeURIComponent).join("/"),
    process.env.API_INTERNAL_URL || "http://127.0.0.1:8000",
  );
  upstream.search = new URL(request.url).search;
  const headers = new Headers();
  for (const name of ["authorization", "content-type", "idempotency-key"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  try {
    const result = await fetch(upstream, {
      method: request.method,
      headers,
      body: ["POST", "PATCH"].includes(request.method)
        ? await request.arrayBuffer()
        : undefined,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(20000),
    });
    const responseHeaders = new Headers({
      "Content-Type": result.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store",
    });
    for (const name of ["x-request-id", "server-timing"]) {
      const value = result.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(result.body, {
      status: result.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      {
        detail:
          "The memory API is unavailable. Check the local stack and try again.",
      },
      { status: 502 },
    );
  }
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
