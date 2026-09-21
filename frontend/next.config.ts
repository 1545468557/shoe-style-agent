import type { NextConfig } from "next";

/**
 * 后端（FastAPI，8020）**没有 CORS 头**，所以浏览器不能直连；
 * 这里用 Next 的 rewrites 做同源代理：前端只请求 /api/*、/files/*、/assets/*。
 */
const BACKEND = process.env.BACKEND_BASE_URL ?? "http://127.0.0.1:8020";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
      { source: "/files/:path*", destination: `${BACKEND}/files/:path*` },
      { source: "/assets/:path*", destination: `${BACKEND}/assets/:path*` },
    ];
  },
};

export default nextConfig;
