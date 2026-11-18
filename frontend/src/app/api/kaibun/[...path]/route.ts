import { NextRequest } from "next/server";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const host = request.headers.get('host')?.split(':')[0];
  if (host !== 'localhost' && host !== '127.0.0.1') return Response.json({detail:"请从本机页面操作"}, { status: 403 });
  const { path } = await context.params;
  if (path.some((part) => part === "." || part === "..")) return Response.json({detail:"请求路径无效"}, { status: 400 });
  if (request.method === "POST") {
    const origin = request.headers.get("origin");
    // Next may canonicalize 127.0.0.1 to localhost in nextUrl. Both loopback
    // origins are the same local application and are already accepted upstream.
    const allowed = new Set(["http://localhost:3010", "http://127.0.0.1:3010"]);
    if (origin && !allowed.has(origin)) return Response.json({detail:"请从本机页面操作"}, { status: 403 });
  }
  try {
    const connection = JSON.parse(await readFile(resolve(process.cwd(), '../.runtime/connection.json'), 'utf8'));
    const address = new URL(connection.url);
    if (address.hostname !== '127.0.0.1' || address.protocol !== 'http:' || !connection.token) throw Error('Invalid local connection');
    const target = address.origin + '/' + path.map(encodeURIComponent).join('/');
    const response = await fetch(target + request.nextUrl.search, {
      method: request.method,
      headers: { "content-type": request.headers.get("content-type") ?? "application/json",
        'x-kaibun-token': connection.token,
        ...(request.headers.get("if-none-match") ? { "if-none-match": request.headers.get("if-none-match")! } : {}) },
      body: request.method === "GET" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(path.includes("tune") ? 900_000 : 120_000)]),
    });
    return new Response(response.body, { status: response.status, headers: {
      "Content-Type": response.headers.get("content-type") ?? "application/json",
      "Cache-Control": response.headers.get("cache-control") ?? "no-store",
      ...(response.headers.get("content-disposition") ? { "Content-Disposition": response.headers.get("content-disposition")! } : {}),
      ...(response.headers.get("etag") ? { "ETag": response.headers.get("etag")! } : {}),
    } });
  } catch (error) {
    if (error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError")) {
      return Response.json({ detail: "这次处理耗时较长，连接已超时；请稍后刷新查看结果。" }, { status: 504 });
    }
    return Response.json({ detail: "怪文书服务暂时连接不上，请检查独立服务是否运行。" }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
