/** 前端 API 客户端：浏览器只请求同源 /api/*（由 next.config 的 rewrites 代理到 8020）。 */

export type Brief = { category: string; style_keywords: string[]; target_user: string; price_band: string; cost_ceiling: string; missing: string[]; extra_notes?: string; };
export type Direction = { id: number; seq: number; name: string; inspiration: string; palette: { name: string; hex: string }[]; silhouette: string; image_url: string | null; image_status: string; is_placeholder: boolean; score?: number | null };
export type Material = { id: string; name: string; fiber: string; weight_gsm: number; price_yuan_per_m: [number, number]; season: string[]; hand: string[]; reason?: string };
export type Pattern = { id: string; name: string; fit: string; waist_ease_cm: number; hip_ease_cm: number; length_options_cm: number[]; difficulty: string; tags: string[] };
export type Craft = { id: string; name: string; note: string };
export type State = { project: { id: number; name: string; status: string }; gates: Record<string, boolean>; brief: { parsed: Brief } | null; direction?: { id: number; name?: string } | null; directions?: Array<{ id: number }> | null; direction_id?: number | null; sampling?: { state: string } | null; iterations: { no: number; changes: Record<string, number> }[] };
export type Bom = { items: { kind: string; name: string; qty: string; subtotal: number[] }[]; estimated_cost_yuan: number[]; note: string };

/** 后端地址：开发期直连 8020（后端已放行 5180 跨域）。 */
export const BACKEND = process.env.NEXT_PUBLIC_BACKEND_BASE_URL ?? "http://127.0.0.1:8020";

/** 把后端给的相对路径（/assets/…、/files/…）转成可直接用的绝对地址。 */
export const mediaUrl = (path: string | null | undefined): string | undefined =>
  !path ? undefined : path.startsWith("http") ? path : `${BACKEND}${path}`;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND}/api${path}`, { headers: { "content-type": "application/json" }, ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body?.error?.message ?? `请求失败（${res.status}）`);
  return body as T;
}

export const api = {
  listDirections: (id: number) => call<{ items: Direction[] }>(`/project/${id}/directions`),
  scoreDirection: (directionId: number, score: number) => call<{ score: number; message: string }>(`/direction/${directionId}/score`, { method: "POST", body: JSON.stringify({ score }) }),
  scores: () => call<{ scored: number; average: number | null; by_score: Record<string, number> }>("/scores"),
  listProjects: () => call<{ items: Array<{ id: number; name: string; status: string; category: string; progress: Record<string, boolean>; gate_count: number; gate_total: number; thumbnail: string | null }> }>("/projects"),
  library: () => call<{ materials: Array<Record<string, unknown>>; trims: Array<Record<string, unknown>>; patterns: Array<Record<string, unknown>>; crafts: Array<Record<string, unknown>>; sizes: Array<Record<string, unknown>>; note: string }>("/library"),
  health: () => call<{ model_provider: string; image_provider: string; library: Record<string, number> }>("/health"),
  create: (name: string) => call<{ id: number }>("/project", { method: "POST", body: JSON.stringify({ name }) }),
  brief: (id: number, text: string) => call<{ parsed: Brief; provider: string }>(`/project/${id}/brief`, { method: "POST", body: JSON.stringify({ text }) }),
  saveBriefFields: (id: number, fields: { category?: string; style_keywords?: string; target_user?: string; price_band?: string; extra_notes?: string }) =>
    call<{ brief: { parsed: Brief }; edited: boolean; message: string }>(`/project/${id}/brief/fields`, { method: "POST", body: JSON.stringify(fields) }),
  reopenBrief: (id: number) => call<{ status: Record<string, boolean>; message: string }>(`/project/${id}/brief/reopen`, { method: "POST" }),
  gate: (id: number, gate: string, payload: Record<string, unknown> = {}) =>
    call<{ status: Record<string, boolean> }>(`/project/${id}/gate/${gate}`, { method: "POST", body: JSON.stringify(payload) }),
  directions: (id: number, count = 4) => call<{ items: Direction[]; provider: string; count: number }>(`/project/${id}/directions`, { method: "POST", body: JSON.stringify({ count }) }),
  generateMaterials: (id: number, directionId: number) => call<{ items: Array<Record<string, unknown>>; provider: string; note: string }>(`/project/${id}/materials/generate?direction_id=${directionId}`, { method: "POST" }),
  acceptMaterial: (id: number, directionId: number, seq: number) => call<{ material_id: string; note: string }>(`/project/${id}/material/accept`, { method: "POST", body: JSON.stringify({ direction_id: directionId, seq }) }),
  generateCrafts: (id: number) => call<{ crafts: Array<Record<string, unknown>>; provider: string; note: string }>(`/project/${id}/crafts/generate`, { method: "POST" }),
  acceptCrafts: (id: number, names: string[]) => call<{ crafts: string[] }>(`/project/${id}/crafts/accept`, { method: "POST", body: JSON.stringify({ names }) }),
  materials: (id: number, directionId: number) =>
    call<{ items: Material[]; provider: string; note: string }>(`/project/${id}/materials?direction_id=${directionId}`),
  pickMaterial: (id: number, directionId: number, materialId: string) =>
    call<{ material_id: string }>(`/project/${id}/material`, { method: "POST", body: JSON.stringify({ direction_id: directionId, material_id: materialId }) }),
  gateMaterial: (id: number) => call<{ status: Record<string, boolean> }>(`/project/${id}/gate/material`, { method: "POST", body: "{}" }),
  designPattern: (id: number) => call<{ design: Record<string, unknown>; provider: string; note: string }>(`/project/${id}/pattern-design`, { method: "POST" }),
  readPatternDesign: (id: number) => call<{ design: Record<string, unknown> | null; provider: string | null }>(`/project/${id}/pattern-design`),
  acceptPattern: (id: number, crafts: string[]) => call<{ pattern_id: string; crafts: string[] }>(`/project/${id}/pattern/accept`, { method: "POST", body: JSON.stringify({ crafts }) }),
  patterns: (id: number) => call<{ items: Pattern[]; crafts: Craft[] }>(`/project/${id}/patterns`),
  craftSuggestions: (id: number, directionId: number) =>
    call<{ items: Array<Craft & { reason?: string }>; provider: string; note: string }>(`/project/${id}/craft-suggestions?direction_id=${directionId}`, { method: "GET" }),
  pickPattern: (id: number, patternId: string, crafts: string[]) =>
    call<{ pattern_id: string }>(`/project/${id}/pattern`, { method: "POST", body: JSON.stringify({ pattern_id: patternId, crafts }) }),
  gatePattern: (id: number) => call<{ status: Record<string, boolean> }>(`/project/${id}/gate/pattern`, { method: "POST", body: "{}" }),
  iterate: (id: number, changes: Record<string, number>) =>
    call<{ iteration_no: number; effects: string[] }>(`/project/${id}/iterate`, { method: "POST", body: JSON.stringify({ changes }) }),
  bom: (id: number) => call<{ bom: Bom; size_spec: Record<string, string | number> }>(`/project/${id}/bom`, { method: "POST", body: "{}" }),
  exportAll: (id: number) => call<{ word: string; excel: string; download: { word: string; excel: string } }>(`/project/${id}/export`, { method: "POST", body: "{}" }),
  sampling: (id: number) => call<Record<string, unknown>>(`/project/${id}/sampling`, { method: "POST", body: "{}" }),
  approve: (id: number) => call<{ state: string }>(`/project/${id}/sampling/advance`, { method: "POST", body: JSON.stringify({ to_state: "approved" }) }),
  state: (id: number) => call<State>(`/project/${id}/state`),
};
