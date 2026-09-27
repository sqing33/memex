import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The site is a PRERENDER artifact, not an SPA: vite compiles the TSX and the CSS,
// then build/prerender.mjs calls renderToString to write each page (tech-design 2.7.2).
// So there is no SSR plugin and no client index.html template - prerender owns the HTML.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // The real artifacts are the prerendered html files; vite output is only raw material.
    lib: {
      entry: 'src/render.tsx',
      formats: ['es'] as const,
      fileName: 'site',
    },
    rollupOptions: {
      external: ['react', 'react-dom', 'react-dom/server'],
      output: { assetFileNames: 'assets/site.[ext]' },
    },
  },
})
