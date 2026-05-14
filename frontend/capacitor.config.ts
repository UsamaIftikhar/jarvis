import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.jarvis.app",
  appName: "JARVIS",
  webDir: "out",
  plugins: {
    SplashScreen: {
      launchAutoHide: true,
      backgroundColor: "#000000",
      showSpinner: false,
    },
  },
  ios: {
    contentInset: "automatic",
    backgroundColor: "#000000",
    allowsLinkPreview: false,
  },
};

export default config;
