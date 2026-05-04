/** @type {import('next').NextConfig} */
const isTauri =
  process.env.TAURI_BUILD === "1" || Boolean(process.env.TAURI_PLATFORM);

const nextConfig = {
  reactStrictMode: true,
  // Static export only for `pnpm build:tauri` (Tauri bundles `out/`). Plain
  // `next dev` / `next build` stay server-mode for normal web use.
  ...(isTauri
    ? {
        output: "export",
        distDir: "out",
        images: { unoptimized: true },
        // Baked into the client bundle — desktop WebView talks to local backend.
        env: {
          NEXT_PUBLIC_JARVIS_WS_URL: "ws://127.0.0.1:8000/ws",
        },
      }
    : {}),
};

export default nextConfig;
