import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import Page from "@/app/page";
import { mediaUrl } from "@/lib/api";

// 每次用例后清空 DOM，避免多次 render 叠加导致"找到多个元素"
afterEach(cleanup);

const state = {
  project: { id: 1, name: "新方案", status: "draft" },
  gates: { brief: false, direction: false, material: false, pattern: false, sampling: false },
  brief: null, material: null, pattern: null, direction: null, iterations: [],
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const body = String(url).includes("/state") ? state : { id: 1 };
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }));
});

describe("工作台首屏", () => {
  it("渲染企划输入、五道确认与推进按钮", async () => {
    render(<Page />);
    await waitFor(() => expect(screen.getByText(/做什么样的衣服/)).toBeTruthy());
    expect(screen.getByText("开始理解")).toBeTruthy();
    expect(screen.getAllByText(/企划/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/打样/).length).toBeGreaterThan(0);
  });

  it("首屏展示示例数据与仿真实例口径", async () => {
    render(<Page />);
    await waitFor(() => expect(screen.getAllByText(/示例数据/).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/仿真实例/).length).toBeGreaterThan(0);
  });

  it("媒体相对路径转绝对地址", () => {
    expect(mediaUrl("/assets/a.png")).toBe("http://127.0.0.1:8020/assets/a.png");
    expect(mediaUrl("http://x/y.png")).toBe("http://x/y.png");
    expect(mediaUrl(null)).toBeUndefined();
  });
});
