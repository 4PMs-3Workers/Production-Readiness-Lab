import type { NextConfig } from 'next';
import { fileURLToPath } from 'node:url';

if (!['api', 'mock'].includes(process.env.NEXT_PUBLIC_APP_MODE ?? 'api')) {
  throw new Error('NEXT_PUBLIC_APP_MODE must be api or mock.');
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // Opt in only for the container; regular builds retain next start support.
  ...(process.env.NEXT_OUTPUT_STANDALONE === '1' ? { output: 'standalone' as const } : {}),
  outputFileTracingRoot: fileURLToPath(new URL('../..', import.meta.url)),
  async rewrites() {
    const upstream = process.env.API_UPSTREAM_URL;
    if (!upstream) return [];
    const url = new URL(upstream);
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== '/'
    ) {
      throw new Error('API_UPSTREAM_URL must be an HTTP(S) origin without credentials or a path.');
    }
    return [{ source: '/api/v1/:path*', destination: `${url.origin}/api/v1/:path*` }];
  },
  turbopack: {
    root: fileURLToPath(new URL('../..', import.meta.url)),
  },
};

export default nextConfig;
