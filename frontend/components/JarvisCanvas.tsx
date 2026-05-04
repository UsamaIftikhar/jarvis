"use client";

import { useEffect, useRef } from "react";
import { useJarvis } from "@/lib/state";
import { createScene, type SceneController } from "@/lib/threeScenes";

const ORB_GLOW: Record<string, string> = {
  IDLE:      "radial-gradient(ellipse at 50% 50%, rgba(0,212,255,0.12) 0%, rgba(0,212,255,0.04) 30%, transparent 65%)",
  LISTENING: "radial-gradient(ellipse at 50% 50%, rgba(0,255,255,0.22) 0%, rgba(0,212,255,0.08) 28%, transparent 60%)",
  THINKING:  "radial-gradient(ellipse at 50% 50%, rgba(255,192,68,0.16) 0%, rgba(0,212,255,0.06) 30%, transparent 62%)",
  SPEAKING:  "radial-gradient(ellipse at 50% 50%, rgba(0,255,255,0.28) 0%, rgba(0,212,255,0.10) 25%, transparent 58%)",
  ERROR:     "radial-gradient(ellipse at 50% 50%, rgba(255,58,85,0.18)  0%, rgba(255,58,85,0.05) 30%, transparent 62%)",
};

export function JarvisCanvas() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sceneRef  = useRef<SceneController | null>(null);
  const state     = useJarvis((s) => s.state);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const scene = createScene(canvas);
    sceneRef.current = scene;
    const handleResize = () => scene.resize(window.innerWidth, window.innerHeight);
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      scene.dispose();
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    sceneRef.current?.setState(state);
  }, [state]);

  return (
    <>
      <canvas
        ref={canvasRef}
        className="fixed inset-0 h-screen w-screen"
        aria-hidden
      />
      {/* CSS radial glow behind the Three.js core — adds depth and richness */}
      <div
        className="pointer-events-none fixed inset-0 transition-all duration-700"
        style={{ background: ORB_GLOW[state] ?? ORB_GLOW.IDLE }}
        aria-hidden
      />
    </>
  );
}
