import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "款式工场 · 鞋服智能设计",
  description: "说清楚要做什么样的衣服，出三个方向、配面料与版型，算好用料与尺寸。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="wrap">
          <header className="bar">
            <b>款式工场</b>
            <span>AI Design Studio</span>
            <nav><a href="/#settings" style={{ color: "inherit" }}>设置</a></nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
