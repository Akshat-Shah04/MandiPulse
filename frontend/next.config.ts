import type { NextConfig } from "next";

// Two ways to build:
//  - normal (dev / full app): the browser calls /api/* on this origin and Next
//    forwards it to the Django REST API, so no CORS setup is needed.
//  - hosted demo (NEXT_PUBLIC_STATIC_DATA=1): a plain static site in out/ that
//    reads the JSON snapshot in public/data. Used for GitHub Pages.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";
const STATIC = process.env.NEXT_PUBLIC_STATIC_DATA === "1";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const nextConfig: NextConfig = STATIC
  ? {
      output: "export",
      basePath: BASE_PATH,
      devIndicators: false,
    }
  : {
      devIndicators: false, // keep the dashboard clean in dev (and in screenshots)
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
      },
    };

export default nextConfig;
