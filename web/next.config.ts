import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Vercel uses Next's output file tracing to figure out which files to include
  // in the serverless function bundle. The onboarding prompt lives outside web/
  // (canonical path: worker/src/product_search/onboarding/prompts/onboard_v1.txt),
  // so we point tracing at the repo root and explicitly include the prompt file.
  outputFileTracingRoot: path.join(__dirname, ".."),
  outputFileTracingIncludes: {
    "/api/onboard/chat": [
      "../worker/src/product_search/onboarding/prompts/**",
    ],
  },
};

export default nextConfig;
