import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The repo root (one level up) now has its own package-lock.json (for the
  // `npm run dev` orchestration script), which Turbopack otherwise treats as
  // an ambiguous second workspace root and warns about on every start.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
