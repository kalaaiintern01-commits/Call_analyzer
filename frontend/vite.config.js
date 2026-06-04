import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// This app builds two ways from the same source (federation-cookbook §4.3):
//   - Standalone:  `npm run dev` / `npm run preview` -> served at /
//   - Federated:   `npm run build` -> served by IIS at /call-center/ as a
//                  remote consumed by the KALA shell.
//
// The exposed entry (`./src/App.jsx`) is router-less; <BrowserRouter> is
// only added in `main.jsx` for standalone mode.
export default defineConfig(({ mode }) => ({
  // CRITICAL — must match the URL path the build is served under in prod.
  // In dev/preview we serve at the dev server root, so `/`.
  base: mode === 'production' ? '/call-center/' : '/',

  plugins: [
    react(),
    federation({
      name: 'call-center',
      filename: 'remoteEntry.js',
      exposes: {
        './App': './src/App.jsx',
      },
      // Must stay byte-identical with the shell — see version-standardization
      // Tier 1. Drift here causes "invalid hook call" at runtime.
      shared: ['react', 'react-dom', 'react-router-dom'],
    }),
  ],

  build: {
    target: 'esnext',     // federation needs modern ESM output
    modulePreload: false, // federation manages its own preload
    minify: false,        // easier to debug remote bugs in prod
    cssCodeSplit: false,  // single CSS file the shell appends on chunk load
  },

  server: {
    host: true,
    port: 5178,           // free slot after Interviewer Bot (5177)
    strictPort: true,
    proxy: {
      // Standalone-mode only — in federated mode IIS reverse-proxies /api
      // to the backend on :8006 (see deploy-backend.yml + IIS URL Rewrite).
      '/api': {
        target: 'http://127.0.0.1:8006',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 5178,
    strictPort: true,
  },
}))
