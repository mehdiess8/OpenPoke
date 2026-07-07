import envPackage from '@next/env';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

// Ensure the Next.js runtime loads environment variables declared in the repo root .env
const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..');
const isDevelopment = process.env.NODE_ENV !== 'production';

const { loadEnvConfig } = envPackage;
loadEnvConfig?.(repoRoot, isDevelopment);

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Proxy WebRTC signaling to the Pipecat voice worker (:7860) so the browser
  // talks same-origin (no CORS) and the worker URL stays server-side config.
  async rewrites() {
    const voiceWorker = process.env.VOICE_WORKER_URL || 'http://localhost:7860';
    return [
      {
        source: '/voice-worker/:path*',
        destination: `${voiceWorker.replace(/\/$/, '')}/:path*`,
      },
    ];
  },
};

export default nextConfig;
