"use client";

/** 工作台（阶段 3 正式前端 · 第一版）：企划 → 挑方向（可并排对比）→ 面料与版型 → 用料与导出。
 *  口径：示例数据必须标注；AI 生成图必须标注"非实物照片"；成本标"估算值"；打样标"演示流程"。 */

import { useCallback, useEffect, useState } from "react";
import { api, mediaUrl, type Bom, type Craft, type Direction, type Material, type Pattern, type SizeRow, type SizeSpec, type State, type TrimSuggestion } from "@/lib/api";

const STEP_LABELS = ["输入企划", "挑一个方向", "定面料与版型", "算用料与尺寸", "打样审批"];
const GATE_LABELS: Record<string, string> = { brief: "企划", direction: "方向", material: "面料", pattern: "版型", sampling: "打样" };

/** 兼容旧结构（只有 labels + bust/waist/…）：转成"按部位渲染"的行；值为 0 或缺的部位不渲染。 */
function defaultRows(meta: Record<string, unknown>, isShoe: boolean, lenLabel: string): SizeRow[] {
  const labels = (meta["labels"] && typeof meta["labels"] === "object" ? meta["labels"] : {}) as Record<string, string>;
  const fallback: Record<string, string> = isShoe
    ? { bust: "鞋码（＝脚长）", waist: "", hip: "跖围", skirt_length: "筒高", sleeve_length: "" }
    : { bust: "胸围", waist: "腰围", hip: "臀围", skirt_length: lenLabel, sleeve_length: "袖长" };
  const rows: SizeRow[] = [];
  for (const key of ["bust", "waist", "hip", "skirt_length", "sleeve_length"]) {
    const label = labels[key] || fallback[key];
    const value = meta[key];
    const empty = value === undefined || value === null || value === "" || Number(value) === 0;
    if (label && !empty) rows.push({ key, label, value: value as string | number, unit: String(meta["unit"] ?? "cm") });
  }
  return rows;
}

export default function Page() {
  const [pid, setPid] = useState<number | null>(null);
  const [st, setSt] = useState<State | null>(null);
  const [text, setText] = useState("夏天度假风连衣裙，25-35岁都市女性，显瘦、面料不易皱，商场吊牌价 400-600 元，拍照上镜");
  const [dirs, setDirs] = useState<Direction[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [mats, setMats] = useState<{ items: Material[]; provider: string; note: string } | null>(null);
  const [pats, setPats] = useState<{ items: Pattern[]; crafts: Craft[] } | null>(null);
  const [crafts, setCrafts] = useState<string[]>([]);
  const [craftNote, setCraftNote] = useState("");
  const [bom, setBom] = useState<{ bom: Bom; size_spec: SizeSpec } | null>(null);
  const [trims, setTrims] = useState<TrimSuggestion[]>([]);
  const [trimSel, setTrimSel] = useState<number[]>([]);
  const [trimNote, setTrimNote] = useState("");
  const [files, setFiles] = useState<{ word: string; excel: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [zoom, setZoom] = useState<string | null>(null);
  const [cmp, setCmp] = useState<Direction[] | null>(null);
  const [change, setChange] = useState({ skirt_length_cm: 10, waist_ease_cm: -2 });
  const [bf, setBf] = useState({ category: "", style_keywords: "", target_user: "", price_band: "", extra_notes: "" });
  const [edited, setEdited] = useState(false);
  const [samplingMsg, setSamplingMsg] = useState("");
  const [view, setView] = useState<number | null>(null);
  const [dirCount, setDirCount] = useState(4);
  const [selectedDirection, setSelectedDirection] = useState<string>("");
  const [pickedMaterial, setPickedMaterial] = useState<string>("");
  const [pickedPattern, setPickedPattern] = useState<string>("");
  const [design, setDesign] = useState<Record<string, unknown> | null>(null);
  const [designNote, setDesignNote] = useState("");
  const [page, setPage] = useState<"work" | "designs" | "library" | "settings">("work");
  const [designs, setDesigns] = useState<Array<Record<string, unknown>>>([]);
  const [lib, setLib] = useState<Record<string, unknown> | null>(null);
  const [libTab, setLibTab] = useState("materials");
  const [scoreInfo, setScoreInfo] = useState<{ scored: number; average: number | null } | null>(null);   // 要生成几个方向（花钱随数量增加）   // null＝跟到最远已达成的那一步

  const refresh = useCallback(async (id: number) => setSt(await api.state(id)), []);
  // 打开工作台不再自动建空方案（历史记录会被空壳塞满）；点「开始理解」时才真正创建
  const ensureProject = async (): Promise<number> => {
    if (pid) return pid;
    const created = await api.create("新方案");
    setPid(created.id);
    return created.id;
  };

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setMsg("");
    try {
      await fn();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy("");
    }
  };
  // 加价倍率＝企划吊牌价中值 ÷ 成本中值（都是估算，仅作参考；解析不到价格就不显示）
  const priceMultiple = (() => {
    const band = st?.brief?.parsed?.price_band ?? "";
    const nums = (band.match(/\d+/g) ?? []).map(Number);
    const cost = bom?.bom?.estimated_cost_yuan;
    if (!nums.length || !cost) return "";
    const retail = nums.length > 1 ? (nums[0] + nums[1]) / 2 : nums[0];
    const mid = (cost[0] + cost[1]) / 2;
    return mid ? (retail / mid).toFixed(1) + " 倍" : "";
  })();
  // 解析结果同步到可编辑表单（保存后刷新会回填）
  useEffect(() => {
    const p = st?.brief?.parsed;
    if (p) setBf({
      category: p.category ?? "",
      style_keywords: (p.style_keywords ?? []).join(", "),
      target_user: p.target_user ?? "",
      price_band: p.price_band ?? "",
      extra_notes: p.extra_notes ?? "",
    });
  }, [st?.brief?.parsed]);

  // 已选面料从 state 回显（刷新后也能看到选了哪一块）
  useEffect(() => {
    const picked = (st as unknown as { material?: { id?: string } } | null)?.material?.id;
    if (picked) setPickedMaterial(picked);
    const pat = (st as unknown as { pattern?: { id?: string } } | null)?.pattern?.id;
    if (pat) setPickedPattern(pat);
  }, [st]);

  const gates = st?.gates ?? {};

  // 尺寸表：**部位名与单位都由后端按品类给出**（鞋→鞋码/跖围；服装→胸腰臀/衣长），前端不写死女装部位。
  // 品类判定也不依赖 bom（未算料时也要正确，否则凉鞋会显示"腰围松量"）。
  const sizeMeta = (bom?.size_spec ?? {}) as SizeSpec & Record<string, unknown>;
  const designCategory = String((design as { category?: string } | null)?.category ?? "");
  const categoryText = `${st?.brief?.parsed?.category ?? ""} ${designCategory} ${String(sizeMeta["system"] ?? "")}`;
  const isShoe = String(sizeMeta["unit"] ?? "") === "mm" || /鞋|靴|跟|凉|拖/.test(categoryText);
  const isBoot = /靴/.test(categoryText);
  const lenLabel = /裤/.test(categoryText) ? "裤长" : /裙/.test(categoryText) ? "裙长" : "衣长";
  const sizeUnit = String(sizeMeta["unit"] ?? (isShoe ? "mm" : "cm"));
  const sizeRows: SizeRow[] = (sizeMeta.rows && sizeMeta.rows.length ? sizeMeta.rows : defaultRows(sizeMeta, isShoe, lenLabel)) as SizeRow[];
  const sizeTiers: Array<Record<string, string | number>> = sizeMeta.tiers ?? [];
  const maxStep = !gates.brief ? 0 : !gates.direction ? 1 : !gates.material || !gates.pattern ? 2 : 3;
  const step = Math.min(view ?? maxStep, maxStep);

  // 辅料建议：进入「用料与尺寸」时读取已生成的（零费用，刷新回显）
  useEffect(() => {
    if (!pid || step < 3) return;
    api.readTrims(pid)
      .then((r) => {
        setTrims(r.items);
        setTrimSel(r.accepted.length ? r.accepted : r.items.map((i) => i.seq));
      })
      .catch(() => undefined);
  }, [pid, step]);

  const GATE_LABELS_CN: Record<string, string> = { brief: "企划", direction: "方向", material: "面料", pattern: "版型", sampling: "打样" };
  const openDesigns = async () => {
    setPage("designs");
    try {
      const r = await api.listProjects();
      setDesigns(r.items);
    } catch {
      setMsg("读不到历史方案，请确认后端在运行");
    }
  };
  // 清空工作台，开始做新的一件
  const resetWorkspace = () => {
    setPid(null);
    setSt(null);
    setDirs([]);
    setMats(null);
    setPats(null);
    setBom(null);
    setFiles(null);
    setCrafts([]);
    setCraftNote("");
    setSelectedDirection("");
    setPickedMaterial("");
    setPickedPattern("");
    setEdited(false);
    setSamplingMsg("");
    setMsg("");
    setPage("work");
  };

  const openSettings = async () => {
    setPage("settings");
    try {
      setScoreInfo(await api.scores());
    } catch {
      setScoreInfo(null);
    }
  };
  // 顶栏「设置」链接（/#settings）→ 打开设置页
  useEffect(() => {
    const sync = () => {
      if (window.location.hash === "#settings") void openSettings();
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const openLibrary = async () => {
    setPage("library");
    try {
      setLib(await api.library());
    } catch {
      setMsg("读不到素材库，请确认后端在运行");
    }
  };
  const openDesign = async (id: number) => {
    setPid(id);
    setPage("work");
    setSelectedDirection("");
    setPickedMaterial("");
    setPickedPattern("");
    setMats(null);
    setPats(null);
    setBom(null);
    try {
      // 载入该方案已有的方向图（只读，不重新出图、不花钱）
      const r = await api.listDirections(id);
      setDirs(r.items);
    } catch {
      setDirs([]);
    }
    await refresh(id);
  };

  return (
    <main className="pad">
      <div className="row" style={{ justifyContent: "flex-start", marginTop: 16 }}>
        <button className={page === "work" ? "btn sm primary" : "btn sm"} onClick={() => setPage("work")}>工作台</button>
        <button className={page === "designs" ? "btn sm primary" : "btn sm"} onClick={() => void openDesigns()}>我的设计（历史记录）</button>
        <button className={page === "library" ? "btn sm primary" : "btn sm"} onClick={() => void openLibrary()}>素材库（示例数据）</button>
      </div>
      <div className="gates">
        <span className="tiny">示例数据 · 仿真实例</span>
        {Object.entries(GATE_LABELS).map(([key, label]) => (
          <span key={key} className={gates[key] ? "" : "tiny"}>
            {gates[key] ? "✓" : "○"} {label}
          </span>
        ))}
      </div>

      <div className="gates" style={{ borderBottom: 0 }}>
        {STEP_LABELS.map((label, index) => (
          <button
            key={label}
            className={index === step ? "" : "tiny"}
            style={{ background: "none", border: 0, padding: 0, cursor: index <= maxStep ? "pointer" : "not-allowed",
                     opacity: index <= maxStep ? 1 : 0.45, font: "inherit", color: "inherit", letterSpacing: "inherit" }}
            disabled={index > maxStep}
            title={index > maxStep ? "还没走到这一步" : "点这里回到这一步"}
            onClick={() => setView(index === maxStep ? null : index)}
          >
            <b className="n">0{index + 1}</b> {label}
          </button>
        ))}
      </div>

      {/* 真正的首页（还没往前走）不加任何导航；只要流程已推进，就保留「回到当前步骤」，避免回看后出不去 */}
      {maxStep > 0 && (
        <div className="row" style={{ justifyContent: "space-between", marginTop: 18 }}>
          {step > 0 ? <button className="btn sm" onClick={() => setView(step - 1)}>← 上一步</button> : <span />}
          {step < maxStep && (
            <button className="btn sm primary" onClick={() => setView(null)}>回到当前步骤（第 0{maxStep + 1} 步）→</button>
          )}
        </div>
      )}
      {view !== null && view < maxStep && (
        <p className="tiny" style={{ marginTop: 8 }}>
          你正在回看第 0{step + 1} 步（只读回看，内容不会被改动）。当前流程在第 0{maxStep + 1} 步；点右边的「回到当前步骤」继续往下做。
        </p>
      )}

      {msg && <div style={{ position: "fixed", left: "50%", transform: "translateX(-50%)", bottom: 28, zIndex: 60,
        background: "#1b1917", color: "#fff", padding: "14px 20px", maxWidth: "80vw",
        border: "1px solid rgba(255,255,255,.25)", fontSize: 14, letterSpacing: ".04em" }}>
        {msg}
        <button className="btn sm" style={{ marginLeft: 14, borderColor: "rgba(255,255,255,.4)", color: "#fff" }}
          onClick={() => setMsg("")}>知道了</button>
      </div>}

      {/* ① 输入企划 */}
      {step === 0 && (
        <>
          <h1>先说说你要<br />做什么样的衣服</h1>
          <p className="lead">用平常的话写就行，不用整理格式。系统看完会告诉你它理解到什么，缺的信息会写成「未提供」。</p>
          <textarea rows={4} value={text} onChange={e => setText(e.target.value)} />
          <div className="row">
            <button className="btn primary" disabled={!!busy} onClick={() => run("理解中", async () => {
              // 打开页面不再自动建方案 → 这里必须先确保有方案，再解析
              const id = await ensureProject();
              await api.brief(id, text);
              await refresh(id);
            })}>{busy === "理解中" ? "理解中…" : "开始理解"}</button>

          </div>
          {st?.brief?.parsed && (
            <div className="card" style={{ marginTop: 18 }}>
              <div className="label">
                我理解到的
                {edited && <span style={{ color: "#96733a" }}> · 已人工修改</span>}
                {gates.brief && <span className="tiny"> · 企划已确认（已锁定）</span>}
              </div>
              <div className="spec">
                <span>品类</span>
                <span className="v"><input value={bf.category} readOnly={gates.brief} onChange={e => setBf({ ...bf, category: e.target.value })} /></span>
                <span>风格</span>
                <span className="v"><input value={bf.style_keywords} readOnly={gates.brief} onChange={e => setBf({ ...bf, style_keywords: e.target.value })} /></span>
                <span>人群</span>
                <span className="v"><input value={bf.target_user} readOnly={gates.brief} onChange={e => setBf({ ...bf, target_user: e.target.value })} /></span>
                <span>价格带</span>
                <span className="v"><input value={bf.price_band} readOnly={gates.brief} onChange={e => setBf({ ...bf, price_band: e.target.value })} /></span>
              </div>
              <p className="hint" style={{ marginTop: 10 }}>
                {(st.brief.parsed.missing ?? []).length
                  ? `你没说：${st.brief.parsed.missing.join("、")}`
                  : "企划要素齐全"}
              </p>
              <div style={{ marginTop: 14 }}>
                <div className="label" style={{ marginBottom: 8 }}>还有什么要补充的？（可留空）</div>
                <input
                  style={{ width: "100%", padding: "12px", border: "1px solid var(--line)", borderRadius: 0, font: "14px/1.5 inherit", color: "var(--ink)", background: "var(--paper)" }}
                  placeholder="比如：要能配高跟鞋；肩宽的人穿也好看；不要露背"
                  value={bf.extra_notes}
                  readOnly={gates.brief}
                  onChange={e => setBf({ ...bf, extra_notes: e.target.value })}
                />
              </div>
              <div className="row" style={{ justifyContent: "flex-start", marginTop: 12 }}>
                {!gates.brief ? (
                  <span className="tiny">改完点下面的「确认，开始出效果图」</span>
                ) : (
                  <>
                    <button className="btn sm" disabled={!!busy} onClick={() => run("重新编辑", async () => {
                      if (!pid) return;
                      await api.reopenBrief(pid);
                      await refresh(pid);
                    })}>重新编辑</button>
                    
                  </>
                )}
              </div>
            </div>
          )}

          {/* 确认放在最下面：先看清（并改好）"我理解到的"，再确认；已确认（锁定）时不再显示，避免误点弹错 */}
          {st?.brief && !gates.brief && (
            <div className="row" style={{ justifyContent: "flex-start", marginTop: 24 }}>
              <button className="btn primary" disabled={!!busy} onClick={() => run("确认中", async () => {
                if (!pid) return;
                // 先把人工修改（含补充说明）存下来，再确认；确认后字段锁定
                await api.saveBriefFields(pid, { category: bf.category, style_keywords: bf.style_keywords, target_user: bf.target_user, price_band: bf.price_band, extra_notes: bf.extra_notes });
                setEdited(true);
                await api.gate(pid, "brief");
                await refresh(pid);
              })}>确认，开始出效果图</button>
              
            </div>
          )}
        </>
      )}

      {/* ② 挑方向 */}
      {step === 1 && (
        <>
          <h1>四个方向<br />挑一个</h1>
          <p className="lead">勾两个可以并排对比；点图看细节。挑中之后才进入面料与版型。</p>
          <div className="row">
            <label className="hint" style={{ display: "flex", gap: 8, alignItems: "center" }}>
              要几个方向
              <select value={dirCount} onChange={e => setDirCount(Number(e.target.value))}
                style={{ padding: "8px 10px", border: "1px solid var(--line)", background: "var(--paper)", color: "var(--ink)", font: "inherit" }}>
                {[3, 4, 5, 6].map(n => <option key={n} value={n}>{n} 个</option>)}
              </select>
              
            </label>
            <button className="btn primary" disabled={!!busy} onClick={() => run("出图中", async () => {
              if (!pid) return;
              const r = await api.directions(pid, dirCount);
              setDirs(r.items);
              await refresh(pid);
            })}>{busy === "出图中" ? "正在生成…（约 30 秒/张）" : dirs.length ? "重新生成" : `生成 ${dirCount} 个方向`}</button>
            <button className="btn" disabled={picked.length !== 2} onClick={() =>
              setCmp(picked.map(id => dirs.find(d => d.id === id)!).filter(Boolean))
            }>并排对比（勾两个）</button>
          </div>
          <div className="cards">
            {dirs.map(d => (
              <figure key={d.id}>
                <div className="pic" onClick={() => d.image_url && setZoom(mediaUrl(d.image_url) ?? null)}>
                  {d.image_url ? <img src={mediaUrl(d.image_url)} alt="" /> : <div style={{ padding: 20 }} className="hint">还没有图</div>}
                  <span className="idx">0{d.seq}</span>
                </div>
                <figcaption className="tiny" style={{ paddingTop: 10 }}>
                  {d.is_placeholder ? "占位图（未接真实出图）" : "AI 生成示意图（非实物照片）"}
                </figcaption>
                <h2 style={{ marginTop: 8, fontSize: 20 }}>{d.name}</h2>
                <p className="hint">{d.inspiration}</p>
                <div className="row" style={{ justifyContent: "center", marginTop: 12 }}>
                  {[1, 2, 3].map(n => (
                    <button key={n} className={d.score === n ? "btn sm primary" : "btn sm"} onClick={() => run("打分", async () => {
                      await api.scoreDirection(d.id, n);
                      setDirs(prev => prev.map(x => (x.id === d.id ? { ...x, score: n } : x)));
                    })}>{n} 分</button>
                  ))}
                </div>
                <p className="tiny" style={{ textAlign: "center", marginTop: 6 }}>
                  {d.score ? `你给这张打了 ${d.score} 分` : "还没打分"}
                </p>
                <div className="row">
                  <button className="btn sm primary" onClick={() => run("选方向", async () => {
                    if (!pid) return;
                    await api.gate(pid, "direction", { direction_id: d.id });
                    setSelectedDirection(String(d.id));
                    await refresh(pid);
                  })}>{selectedDirection === String(d.id) ? "✓ 已选这个方向" : "就用这个方向"}</button>
                  <label className="hint" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input type="checkbox" checked={picked.includes(d.id)} onChange={e =>
                      setPicked(e.target.checked ? [...picked, d.id].slice(-2) : picked.filter(x => x !== d.id))} />
                    加入对比
                  </label>
                </div>
              </figure>
            ))}
          </div>
        </>
      )}

      {/* ③ 面料与版型 */}
      {step === 2 && pid && (
        <>
          <h1>选面料<br />定版型</h1>
          <div className="grid2">
            <div>
              <div className="label">面料搭配</div>
              {!mats && <button className="btn sm" disabled={!!busy} onClick={() => run("配面料中", async () => {
                const directionId = st?.direction_id ?? dirs[0]?.id;
                if (!directionId) throw new Error("还没有选定的方向，请先回到上一步点「就用这个方向」");
                setMats(await api.materials(pid, directionId));
              })}>{busy === "配面料中" ? "正在配面料…" : "帮我配面料"}</button>}
              {mats && (
                <>
                  <p className="hint" style={{ marginTop: 10 }}>{mats.note}</p>
                  {mats.items.map(m => (
                    <div className="card" key={m.id}>
                      <div className="spec"><h3>{m.name}</h3><span className="v n">{m.price_yuan_per_m[0]}~{m.price_yuan_per_m[1]} 元/米</span></div>
                      <p className="hint">{m.fiber} · {m.weight_gsm} g/m² · {m.hand.join("、")}</p>
                      {m.reason && <p className="hint" style={{ marginTop: 6 }}>理由：{m.reason}</p>}
                      <div className="row">
                        <button className="btn sm" onClick={() => run("选面料", async () => {
                          const directionId = st?.direction_id ?? dirs[0]?.id;
                          if (!directionId) throw new Error("还没有选定的方向，请先回到上一步点「就用这个方向」");
                          await api.pickMaterial(pid, directionId, m.id);
                          setPickedMaterial(m.id);
                        })}>{pickedMaterial === m.id ? "✓ 已选这块" : "用这个"}</button>
                      </div>
                    </div>
                  ))}
                  {mats && (
                    <p className="hint" style={{ marginTop: 14 }}>
                      这三块都不合适？
                      <button className="btn sm" style={{ marginLeft: 10 }} disabled={!!busy} onClick={() => run("配面料中", async () => {
                        const directionId = st?.direction_id ?? dirs[0]?.id;
                        if (!directionId) throw new Error("还没有选定的方向，请先回到上一步点「就用这个方向」");
                        setMats(await api.materials(pid, directionId));
                        setPickedMaterial("");
                      })}>{busy === "配面料中" ? "正在配面料…" : "再配一批"}</button>
                      
                    </p>
                  )}
                  {pickedMaterial && (
                    <p className="hint" style={{ marginTop: 12, color: "#96733a" }}>
                      已选：{mats.items.find(x => x.id === pickedMaterial)?.name ?? pickedMaterial} 面料
                    </p>
                  )}
                  <button className="btn primary" disabled={!!busy || !pickedMaterial} onClick={() => run("确认面料", async () => {
                    await api.gateMaterial(pid);
                    await refresh(pid);
                  })}>{pickedMaterial ? "面料就它，下一步" : "先点「用这个」选一块面料"}</button>
                  <p className="tiny" style={{ marginTop: 10 }}>面料来自示例库（仿真实例）；接入企业物料库后自动替换。</p>
                </>
              )}
            </div>
            <aside className="side">
              <div className="label">版型与工艺（AI 设计）</div>
              <button className="btn sm" disabled={!!busy} onClick={() => run("设计版型", async () => {
                try {
                  const r = await api.designPattern(pid);
                  setDesign(r.design);
                  setDesignNote(r.note);
                } catch (e) {
                  setMsg(e instanceof Error ? e.message : "版型设计失败");
                }
                try {
                  const p2 = await api.patterns(pid);
                  setPats(p2);
                  const sug = await api.craftSuggestions(pid, Number(st?.direction_id) || dirs[0]?.id || 0);
                  setCrafts(sug.items.map(i => i.id));
                  setCraftNote(sug.note);
                } catch {
                  setCraftNote("");
                }
              })}>{busy === "设计版型" ? "AI 设计中…" : design ? "重新设计版型" : "AI 设计版型"}</button>
              {design && (
                <div className="card" style={{ marginTop: 12 }}>
                  <h3 style={{ textAlign: "left" }}>{String(design.name ?? "")}</h3>
                  <p className="hint">品类 {String(design.category ?? "")}｜廓形 {String(design.silhouette ?? "")}</p>
                  <div className="spec" style={{ marginTop: 8 }}>
                    <span>松量/参数</span>
                    <span className="v">{Object.entries((design.ease as Record<string, number>) ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</span>
                    <span>领 / 袖</span><span className="v">{[design.collar, design.sleeve].filter(Boolean).join(" / ") || "—"}</span>
                    <span>摆 / 长度</span><span className="v">{String(design.hem ?? "—")}{design.length_cm ? ` · ${design.length_cm}cm` : ""}</span>
                  </div>
                  <p className="hint" style={{ marginTop: 8 }}>结构线：{((design.structure_lines as string[]) ?? []).join("、") || "—"}</p>
                  <p className="hint">放码基准：{String(design.size_base ?? "—")}</p>
                  <p className="hint" style={{ marginTop: 6 }}>设计理由：{String(design.reason ?? "—")}</p>
                  <p className="tiny" style={{ marginTop: 8 }}>{designNote}</p>
                </div>
              )}
              {pats && (
                <>
                  <div className="label" style={{ marginTop: 18 }}>工艺（可多选）</div>
                  {craftNote && <p className="tiny" style={{ marginBottom: 10 }}>{craftNote}</p>}
                  {pats.crafts.map(c => (
                    <label key={c.id} className="hint" style={{ display: "block", marginBottom: 6 }}>
                      <input type="checkbox" checked={crafts.includes(c.id)} onChange={e =>
                        setCrafts(e.target.checked ? [...crafts, c.id] : crafts.filter(x => x !== c.id))} /> {c.name}
                    </label>
                  ))}
                  <button className="btn primary" style={{ marginTop: 12 }} disabled={!!busy || !design} onClick={() => run("确认版型", async () => {
                    await api.acceptPattern(pid, crafts);
                    await api.gatePattern(pid);
                    await refresh(pid);
                  })}>{design ? "采纳这个版型，下一步" : "先点「AI 设计版型」"}</button>
                </>
              )}
            </aside>
          </div>
        </>
      )}

      {/* ④ 用料与尺寸 */}
      {step >= 3 && pid && (
        <>
          <h1>用料与尺寸</h1>
          <div className="grid2">
            <div>
              <div className="label">辅料建议（AI 生成 · 非采购数据）</div>
              <p className="tiny">按品类生成（鞋类→内里/鞋垫/大底；服装→里布/拉链/衬布），价格为估算值，非采购报价。</p>
              <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
                <button className="btn sm" disabled={!!busy} onClick={() => run("辅料生成中", async () => {
                  const r = await api.generateTrims(pid);
                  setTrims(r.items);
                  setTrimSel(r.items.map(i => i.seq));
                  setTrimNote(r.note);
                  setMsg(`已生成 ${r.items.length} 条辅料建议，勾选后点「采纳」`);
                })}>{trims.length ? "重新生成辅料建议" : "AI 生成辅料建议（约 ¥0.01）"}</button>
                {trims.length > 0 && (
                  <button className="btn sm primary" disabled={!!busy} onClick={() => run("采纳中", async () => {
                    await api.acceptTrims(pid, trimSel);
                    setBom(await api.bom(pid));
                    setMsg("辅料已采纳，已重新计算用料与成本");
                  })}>采纳选中（{trimSel.length}）</button>
                )}
              </div>
              {trimNote && <p className="tiny">{trimNote}</p>}
              {trims.length > 0 && (
                <div className="card" style={{ padding: "12px 16px", marginBottom: 20 }}>
                  {trims.map(t => (
                    <label key={t.seq} className="spec" style={{ gridTemplateColumns: "auto 1fr", marginBottom: 7 }}>
                      <input type="checkbox" checked={trimSel.includes(t.seq)} onChange={() => setTrimSel(prev => prev.includes(t.seq) ? prev.filter(s => s !== t.seq) : [...prev, t.seq])} />
                      <span>{t.name}｜{t.spec}｜{t.use}｜{t.price?.[0]}~{t.price?.[1]} {t.unit}{t.why ? `｜${t.why}` : ""}</span>
                    </label>
                  ))}
                </div>
              )}

              <div className="label">用料清单</div>
              <button className="btn sm" disabled={!!busy} onClick={() => run("算料中", async () => setBom(await api.bom(pid)))}>
                {bom ? "重新计算" : "算用料与尺寸"}
              </button>
              {bom && (
                <div style={{ marginTop: 14 }}>
                  <div className="card" style={{ padding: "14px 18px" }}>
                    {bom.bom.items.map(i => (
                      <div className="spec" key={`${i.kind}-${i.name}`} style={{ marginBottom: 7 }}>
                        <span>{i.kind}｜{i.name}</span>
                        <span className="v">
                          {i.qty} · {i.subtotal.some(v => v) ? `${i.subtotal[0]}~${i.subtotal[1]} 元` : "不计入成本"}
                        </span>
                      </div>
                    ))}
                  </div>
                  {bom.bom.trims_source && <p className="tiny">辅料来源：{bom.bom.trims_source}</p>}
                  <div className="deep">
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 28 }}>
                      <div>
                        <div className="label">成本估算（估算值）</div>
                        <div className="sum"><span>合计</span><span className="n">¥{bom.bom.estimated_cost_yuan[0]}–{bom.bom.estimated_cost_yuan[1]}</span></div>
                      </div>
                      <div>
                        <div className="label">企划吊牌价</div>
                        <div className="sum"><span>售价</span><span className="n">{st?.brief?.parsed?.price_band || "未提供"}</span></div>
                        <p className="tiny" style={{ marginTop: 10 }}>
                          {priceMultiple ? `加价倍率约 ${priceMultiple}（吊牌价 ÷ 成本，估算值）` : "企划里没写价格带，无法算加价倍率"}
                        </p>
                      </div>
                    </div>
                    <p className="hint" style={{ marginTop: 8 }}>{bom.bom.note.replace(/\*\*/g, "")}</p>
                  </div>
                  <div className="label" style={{ marginTop: 24 }}>尺寸表（{bom.size_spec.system}）</div>
                  <div className="spec">
                    {sizeRows.map(r => (
                      <span key={r.key} style={{ display: "contents" }}>
                        <span>{r.label}</span><span className="v">{r.value} {r.unit ?? sizeUnit}</span>
                      </span>
                    ))}
                  </div>
                  {sizeTiers.length > 0 && (
                    <table className="tiny" style={{ width: "100%", marginTop: 10, borderCollapse: "collapse" }}>
                      <thead>
                        <tr>
                          {Object.keys(sizeTiers[0]).map(k => (
                            <th key={k} style={{ textAlign: k === "size" ? "left" : "right", padding: "4px 6px", borderBottom: "1px solid #ddd" }}>{k === "size" ? "尺码" : k}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {sizeTiers.map((t, idx) => (
                          <tr key={idx}>
                            {Object.keys(sizeTiers[0]).map(k => (
                              <td key={k} style={{ textAlign: k === "size" ? "left" : "right", padding: "4px 6px", borderBottom: "1px solid #f0f0f0" }}>{t[k]}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  <p className="tiny" style={{ marginTop: 8 }}>单位：{sizeUnit}；各档数值由代码按品类基准 + 版型参数计算，估算值，投产前请版师复核。</p>
                </div>
              )}
            </div>
            <aside className="side">
              <div className="label">改版型</div>
              <div className="spec" style={{ gridTemplateColumns: "1fr auto" }}>
                <span>{isShoe ? (isBoot ? "筒高调整" : "鞋帮高调整") : "长度调整"}</span><span><input type="number" value={change.skirt_length_cm} onChange={e => setChange({ ...change, skirt_length_cm: Number(e.target.value) })} /> cm</span>
                <span>{isShoe ? "跖围调整" : "腰围松量"}</span><span><input type="number" value={change.waist_ease_cm} onChange={e => setChange({ ...change, waist_ease_cm: Number(e.target.value) })} /> {isShoe ? "mm" : "cm"}</span>
              </div>
              <button className="btn sm" style={{ marginTop: 12 }} disabled={!!busy} onClick={() => run("改版中", async () => {
                await api.iterate(pid, change);
                setBom(await api.bom(pid));
              })}>记为新版</button>
              <div className="label" style={{ marginTop: 22 }}>改版记录</div>
              {st?.iterations?.length ? st.iterations.map(r => (
                <div className="spec" key={r.no} style={{ gridTemplateColumns: "auto 1fr" }}>
                  <span>第 {r.no} 版</span>
                  <span className="v">{Object.entries(r.changes).map(([k, v]) => `${k === "skirt_length_cm" ? (isShoe ? (isBoot ? "筒高" : "鞋帮高") : "长度") : (isShoe ? "跖围" : "腰围松量")} ${v > 0 ? "+" : ""}${v}${k === "waist_ease_cm" && isShoe ? "mm" : "cm"}`).join("、")}</span>
                </div>
              )) : <p className="hint">还没改过版。</p>}

              <div className="label" style={{ marginTop: 24 }}>导出与打样</div>
              <button className="btn primary" style={{ width: "100%", marginBottom: 10 }} disabled={!!busy} onClick={() => run("导出中", async () => {
                const r = await api.exportAll(pid);
                setFiles(r.download);
              })}>导出 Word 与 Excel</button>
              {files && (
                <p className="hint">
                  <a href={mediaUrl(files.word)}>下载设计方案</a> · <a href={mediaUrl(files.excel)}>下载用料与尺寸</a>
                </p>
              )}
              <button className="btn" style={{ width: "100%" }} disabled={!!busy} onClick={() => run("打样中", async () => {
                await api.sampling(pid);
                const done = await api.approve(pid);
                setSamplingMsg(`打样单已生成并审批通过（当前状态：${done.state}）`);
                await refresh(pid);
              })}>{samplingMsg ? "重新生成打样单" : "生成打样单并审批"}</button>
              {(samplingMsg || st?.sampling?.state) && (
                <div style={{ marginTop: 12, border: "1px solid var(--gold-line)", padding: "12px 14px", background: "var(--cream)" }}>
                  <p className="hint" style={{ color: "#96733a", marginBottom: 6 }}>
                    {samplingMsg || `打样单已存在（当前状态：${st?.sampling?.state}）`}
                  </p>
                  
                </div>
              )}
              <p className="tiny" style={{ marginTop: 10 }}>打样为演示流程：不接真实工厂系统，单据/状态/回传字段已按标准预留。</p>

              {/* 收尾：这件做完了 */}
              <div className="row" style={{ justifyContent: "flex-start", marginTop: 20 }}>
                <button className="btn primary" onClick={() => void openDesigns()}>完成，回到我的设计</button>
                <button className="btn" onClick={resetWorkspace}>再做一件新的</button>
              </div>
              {!gates.sampling && <p className="tiny">（还没点「生成打样单并审批」；也可以直接点上面结束）</p>}
            </aside>
          </div>
        </>
      )}

      {page !== "work" && (
        <div className="overlay">
          <button className="close" onClick={() => {
            setPage("work");
            if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
          }}>关闭</button>
          <div style={{ maxWidth: 1100, margin: "28px auto 0" }}>
            {page === "designs" && (
              <>
                <h2>我的设计（历史记录）</h2>
                <p className="tiny" style={{ marginBottom: 16 }}>
                  示例数据 · 仿真实例 ｜ 只列出做过内容的方案（空方案不显示）；点「继续做」接着往下做
                </p>
                <div className="cards" style={{ textAlign: "left" }}>
                  {designs.map(d => (
                    <div className="card" key={String(d.id)}>
                      <div className="pic" style={{ marginBottom: 12 }}>
                        {d.thumbnail
                          ? <img src={mediaUrl(String(d.thumbnail))} alt="" />
                          : <div className="hint" style={{ padding: 20 }}>还没有方向图</div>}
                      </div>
                      <h3 style={{ textAlign: "left" }}>#{String(d.id)} {String(d.name)}｜{String(d.category || "未写品类")}</h3>
                      <p className="hint">
                        进度 {String(d.gate_count)}/{String(d.gate_total)}：
                        {Object.entries(d.progress as Record<string, boolean>).filter(([, v]) => v).map(([k]) => GATE_LABELS_CN[k] ?? k).join(" → ") || "还没开始"}
                      </p>
                      <button className="btn sm" style={{ marginTop: 12 }} onClick={() => void openDesign(Number(d.id))}>继续做</button>
                    </div>
                  ))}
                </div>
                {!designs.length && <p className="hint">还没有历史方案：回到工作台点「开始理解」就会自动创建一个。</p>}
              </>
            )}

            {page === "library" && (
              <>
                <h2>素材库（示例数据 · 仿真实例）</h2>
                <p className="tiny" style={{ marginBottom: 14 }}>{String(lib?.note ?? "")}</p>
                <div className="row" style={{ justifyContent: "flex-start", marginTop: 8 }}>
                  {([["materials", "面料"], ["trims", "辅料"], ["patterns", "版型"], ["crafts", "工艺"], ["sizes", "尺码"]] as const).map(([k, label]) => (
                    <button key={k} className={libTab === k ? "btn sm primary" : "btn sm"} onClick={() => setLibTab(k)}>
                      {label}（{((lib?.[k] as unknown[]) ?? []).length}）
                    </button>
                  ))}
                </div>
                <div className="card" style={{ marginTop: 18 }}>
                  {(((lib?.[libTab] as Array<Record<string, unknown>>) ?? [])).map((row, i) => (
                    <div className="spec" key={i} style={{ gridTemplateColumns: "minmax(140px,220px) 1fr", padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
                      <span>{String(row.name ?? row.id ?? "")}</span>
                      <span style={{ textAlign: "left", fontFamily: "inherit", color: "var(--ink)" }}>
                        {Object.entries(row)
                          .filter(([k]) => !["id", "name", "is_sample"].includes(k))
                          .map(([k, v]) => `${k}：${Array.isArray(v) ? v.join("/") : String(v)}`)
                          .join("　｜　")}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}

            {page === "settings" && (
              <>
                <h2>设置</h2>
                <p className="tiny" style={{ marginBottom: 16 }}>只读展示（不含任何密钥；密钥只存在后端 .env，不进前端）</p>
                <div className="card">
                  <div className="spec">
                    <span>AI（文本）</span><span className="v">DeepSeek · deepseek-chat</span>
                    <span>出图</span><span className="v">火山方舟 · doubao-seedream-5-0-lite（2K）</span>
                    <span>示例库</span><span className="v">面料 40 · 辅料 6 · 版型 8 · 工艺 12 · 尺码 4</span>
                    <span>出图质量打分</span>
                    <span className="v">{scoreInfo ? `已评 ${scoreInfo.scored} 张${scoreInfo.average ? `，平均 ${scoreInfo.average} 分` : ""}` : "读取中…"}</span>
                  </div>
                </div>
                <p className="hint" style={{ marginTop: 12 }}>
                  口径声明：示例数据 · 仿真实例 ｜ 效果图为 AI 生成示意图（非实物照片）｜ 成本为估算值 ｜ 打样为演示流程（未对接真实工厂/PLM）
                </p>
              </>
            )}
          </div>
        </div>
      )}

      {zoom && (
        <div className="overlay" onClick={() => setZoom(null)}>
          <button className="close">关闭</button>
          <div style={{ maxWidth: 900, margin: "36px auto 0" }}>
            <img src={zoom} alt="" style={{ width: "100%" }} />
            <p className="tiny" style={{ marginTop: 12 }}>AI 生成示意图（非实物照片）· 点空白关闭</p>
          </div>
        </div>
      )}
      {cmp && (
        <div className="overlay" onClick={() => setCmp(null)}>
          <button className="close">关闭</button>
          <div className="cmp">
            {cmp.map(d => (
              <div key={d.id}>
                {d.image_url && <img src={mediaUrl(d.image_url)} alt="" />}
                <p className="hint" style={{ marginTop: 10, letterSpacing: ".12em" }}>{d.name}｜{d.silhouette}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </main>
  );
}
