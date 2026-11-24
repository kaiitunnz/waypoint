import { fetchTailnetSnapshot } from "@waypoint/web-core/tailnet-server";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return Response.json(await fetchTailnetSnapshot());
}
