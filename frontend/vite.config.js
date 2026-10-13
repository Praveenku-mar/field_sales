import fs from 'fs'
import path from 'path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import frappeui from 'frappe-ui/vite'

// `yarn dev` serves the app on :8080 and proxies /api, /app, /login, /assets,
// /files to the bench web server (:8000, read from common_site_config.json).
// Open it as http://<site>:8080/fieldsales so requests reach the right site.
// If 8080 is taken, run with FS_DEV_PORT=8081 (the proxy plugin ignores --port).
// Vite rejects unknown Host headers. Allow this bench's site names (sites/*/site_config.json),
// which is what the dev server is opened as, and nothing else.
const sitesDir = path.resolve(import.meta.dirname, '../../../sites')
const benchSites = fs.existsSync(sitesDir)
  ? fs.readdirSync(sitesDir).filter((name) => fs.existsSync(path.join(sitesDir, name, 'site_config.json')))
  : []

export default defineConfig({
  plugins: [
    frappeui({
      frappeProxy: { port: Number(process.env.FS_DEV_PORT) || undefined },
      jinjaBootData: true,
      // Its esbuild helper breaks Vite 8's dependency scan; we use no code editor.
      codeLanguages: false,
      buildConfig: {
        outDir: '../field_sales/public/frontend',
        indexHtmlPath: '../field_sales/www/fieldsales.html',
        emptyOutDir: true,
      },
    }),
    vue(),
  ],
  server: {
    allowedHosts: benchSites,
  },
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
    dedupe: ['vue', 'vue-router', 'frappe-ui', 'reka-ui', '@vueuse/core'],
  },
  optimizeDeps: {
    include: ['tailwind.config.js'],
    // frappe-ui ships source; pre-bundling it would load parts of it twice.
    exclude: ['frappe-ui'],
  },
})
