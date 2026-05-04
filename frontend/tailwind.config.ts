import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        jarvis: {
          bg:     "#0a0a0f",
          blue:   "#00d4ff",
          cyan:   "#00ffff",
          panel:  "#0d1117",
          dim:    "#0a1218",
          text:   "#c9f0ff",
          muted:  "#4a6a7a",
          danger: "#ff3a55",
          gold:   "#ffc044",
        },
      },
      fontFamily: {
        display: ["var(--font-orbitron)", "sans-serif"],
        body:    ["var(--font-rajdhani)", "sans-serif"],
      },
      boxShadow: {
        "glow-blue": "0 0 10px rgba(0, 212, 255, 0.7), 0 0 28px rgba(0, 212, 255, 0.28)",
        "glow-cyan": "0 0 14px rgba(0, 255, 255, 0.5), 0 0 36px rgba(0, 255, 255, 0.2)",
        "glow-red":  "0 0 10px rgba(255, 58, 85, 0.7),  0 0 28px rgba(255, 58, 85, 0.28)",
      },
      animation: {
        "scan":          "scan 6s linear infinite",
        "pulse-slow":    "pulse 3s ease-in-out infinite",
        "flicker":       "flicker 5s steps(8, end) infinite",
        "spin-slow":     "spin 12s linear infinite",
        "spin-reverse":  "spin-reverse 9s linear infinite",
        "fade-in":       "fade-in 0.4s ease-out forwards",
        "slide-up":      "slide-up 0.35s ease-out forwards",
        "data-stream":   "data-stream 2s linear infinite",
      },
      keyframes: {
        scan: {
          "0%":   { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100vh)" },
        },
        flicker: {
          "0%, 94%, 100%": { opacity: "1" },
          "95%":            { opacity: "0.35" },
          "96%":            { opacity: "0.85" },
          "97%":            { opacity: "0.5" },
          "98%":            { opacity: "0.9" },
          "99%":            { opacity: "0.65" },
        },
        "spin-reverse": {
          "0%":   { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(-360deg)" },
        },
        "fade-in": {
          "0%":   { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-up": {
          "0%":   { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "data-stream": {
          "0%":   { opacity: "0.3" },
          "50%":  { opacity: "1" },
          "100%": { opacity: "0.3" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
