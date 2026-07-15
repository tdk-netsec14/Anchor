import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // This machine has other package-lock.json files above the project, which
  // makes Next guess the wrong workspace root for file tracing. Pin it here so
  // server bundles and traces resolve against this app.
  outputFileTracingRoot: path.join(import.meta.dirname),
};

export default nextConfig;
