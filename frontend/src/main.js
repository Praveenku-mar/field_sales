import './index.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { FrappeUI, setConfig, frappeRequest, toast } from 'frappe-ui'

import App from './App.vue'
import router from './router'
import './composables/useInstallPrompt' // listens for beforeinstallprompt from page load

// createResource / createListResource / createDocumentResource all fetch through this.
setConfig('resourceFetcher', frappeRequest)
// frappe-ui sends the browser's hostname as X-Frappe-Site-Name, which names no site
// behind a tunnel or proxy (ngrok, a custom domain). Send the site that served the page.
setConfig('requestHeaders', () =>
  window.site_name ? { 'X-Frappe-Site-Name': window.site_name } : {},
)

const app = createApp(App)
app.use(FrappeUI)
app.use(createPinia())

async function start() {
  if (import.meta.env.DEV) {
    // In production, www/fieldsales.py renders these into the page (jinjaBootData).
    const boot = await frappeRequest({
      url: '/api/method/field_sales.www.fieldsales.get_context_for_dev',
      method: 'GET',
    })
    Object.assign(window, boot)
  }
  // Installing the router starts the first navigation, whose guard calls the API:
  // only do it once the boot values (csrf_token) are on window.
  app.use(router)
  app.mount('#app')
  registerServiceWorker()
}

// Precaches the built assets and makes the app installable (see src/sw.js).
function registerServiceWorker() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return
  // Only an update replaces an existing controller; the first install does not.
  const hadController = !!navigator.serviceWorker.controller
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController) toast.success('App updated')
  })
  navigator.serviceWorker.register('/assets/field_sales/frontend/sw.js').catch((error) => {
    console.error('Service worker registration failed', error)
  })
}

start()
