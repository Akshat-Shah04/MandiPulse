import type { NextConfig } from "next";

// Django REST API. In dev the browser calls /api/* on this origin and Next
// forwards it, so no CORS setup is needed.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  devIndicators: false, // keep the dashboard clean in dev (and in screenshots)
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
