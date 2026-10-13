import './index.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { FrappeUI, setConfig, frappeRequest } from 'frappe-ui'

import App from './App.vue'
import router from './router'

// createResource / createListResource / createDocumentResource all fetch through this.
setConfig('resourceFetcher', frappeRequest)

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
}

start()
