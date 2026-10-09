import type { NextConfig } from "next";
const port = Number(process.env.INTERIOR_BACKEND_PORT || "18877");
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid INTERIOR_BACKEND_PORT");
const config: NextConfig = {
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    const api = `http://127.0.0.1:${port}`;
    return ["/api/:path*", "/webapp/:path*", "/room-asset-composer/:path*"].map(source => ({ source, destination: api + source }));
  },
};
export default config;
