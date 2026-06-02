import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'
import tailwindConfig from './tailwind.config'

// PostCSS configured inline so Vite handles it natively without ts-node.
// tailwind.config.ts is imported directly by esbuild (Vite's config bundler).
export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  build: {
    outDir: 'dist',
  },
  css: {
    postcss: {
      plugins: [
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        tailwindcss(tailwindConfig as any),
        autoprefixer,
      ],
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8765',
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
