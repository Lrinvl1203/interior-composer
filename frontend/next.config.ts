import type { NextConfig } from "next";
import { resolve } from "node:path";
import { readBackendPort } from "./lib/serverConfig";
const port = readBackendPort(resolve(__dirname, "..", ".env"));
const config: NextConfig = {
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    const api = `http://127.0.0.1:${port}`;
    return ["/api/:path*", "/webapp/:path*", "/room-asset-composer/:path*"].map(source => ({ source, destination: api + source }));
  },
};
export default config;
