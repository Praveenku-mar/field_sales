import { onBeforeUnmount } from 'vue'
import { io } from 'socket.io-client'

// One socket.io connection to Frappe's realtime server, opened on first use and
// shared by every page. Same host as the page; on a dev server (bench start / yarn dev)
// the socket server listens on its own port (socketio_port), in production nginx
// proxies /socket.io on the page's own origin. Signed in through the sid cookie.
let socket = null
const subscribedDoctypes = new Set()

function getSocket() {
  if (socket) return socket
  let host = window.location.origin
  if (window.dev_server) host = `${window.location.protocol}//${window.location.hostname}:${window.socketio_port || 9000}`
  socket = io(`${host}/${window.site_name}`, {
    withCredentials: true,
    reconnectionAttempts: 5,
    extraHeaders: window.site_name ? { 'X-Frappe-Site-Name': window.site_name } : {},
  })
  // Rooms are per connection: join them again after a reconnect.
  socket.on('connect', () => subscribedDoctypes.forEach((d) => socket.emit('doctype_subscribe', d)))
  socket.on('connect_error', (err) => console.warn('Realtime connection failed:', err.message))
  return socket
}

/**
 * Listen for `event` published to `doctype`'s room (server: publish_realtime(event, ..., doctype=...))
 * while the calling component is mounted.
 */
export function onDoctypeEvent(doctype, event, handler) {
  const s = getSocket()
  if (!subscribedDoctypes.has(doctype)) {
    subscribedDoctypes.add(doctype)
    if (s.connected) s.emit('doctype_subscribe', doctype)
  }
  s.on(event, handler)
  onBeforeUnmount(() => s.off(event, handler))
}
