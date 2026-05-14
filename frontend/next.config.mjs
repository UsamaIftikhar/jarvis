/** @type {import('next').NextConfig} */
const isTauri =
  process.env.TAURI_BUILD === "1" || Boolean(process.env.TAURI_PLATFORM);

const isCapacitor = process.env.CAPACITOR_BUILD === "1";

const nextConfig = {
  reactStrictMode: true,
  ...(isTauri
    ? {
        output: "export",
        distDir: "out",
        images: { unoptimized: true },
        env: {
          NEXT_PUBLIC_JARVIS_WS_URL: "ws://127.0.0.1:8000/ws",
        },
      }
    : isCapacitor
    ? {
        // Static export for Capacitor — backend URL is set at runtime via localStorage.
        output: "export",
        distDir: "out",
        images: { unoptimized: true },
        env: {
          NEXT_PUBLIC_BUILD_TARGET: "capacitor",
        },
      }
    : {}),
};

export default nextConfig;
