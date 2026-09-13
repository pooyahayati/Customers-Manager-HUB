import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

const forwardedResponseHeaders = [
  "cache-control",
  "content-disposition",
  "content-type",
  "etag",
  "location",
  "set-cookie",
  "x-request-id",
] as const;

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const apiOrigin = process.env.CMH_API_BASE_URL ?? "http://127.0.0.1:8000";
  const target = new URL(
    `/api/${path.map((segment) => encodeURIComponent(segment)).join("/")}${request.nextUrl.search}`,
    apiOrigin,
  );
  const headers = new Headers(request.headers);
  headers.delete("connection");
  headers.delete("content-length");
  headers.delete("host");
  headers.delete("transfer-encoding");

  try {
    const upstream = await fetch(target, {
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      cache: "no-store",
      duplex: request.body ? "half" : undefined,
      headers,
      method: request.method,
      redirect: "manual",
    } as RequestInit & { duplex?: "half" });

    const responseHeaders = new Headers();
    for (const name of forwardedResponseHeaders) {
      const value = upstream.headers.get(name);
      if (value !== null) responseHeaders.set(name, value);
    }

    return new Response(request.method === "HEAD" || upstream.status === 204 ? null : upstream.body, {
      headers: responseHeaders,
      status: upstream.status,
    });
  } catch {
    return Response.json({ detail: "API service is unavailable" }, { status: 502 });
  }
}

export const DELETE = proxy;
export const GET = proxy;
export const PATCH = proxy;
export const POST = proxy;
export const PUT = proxy;
