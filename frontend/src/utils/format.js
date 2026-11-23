// Display helpers. Currency comes from the site defaults (System Settings), like the desk.

export function siteCurrency() {
  return window.sysdefaults?.currency || 'INR'
}

export function formatCurrency(value, currency = siteCurrency(), digits = 2) {
  if (value == null || value === '') return '—'
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    }).format(value)
  } catch {
    return `${currency} ${Number(value).toFixed(digits)}`
  }
}

export function formatNumber(value, digits = 0) {
  if (value == null || value === '') return '—'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value)
}

function toDate(value) {
  if (!value) return null
  // Frappe sends "YYYY-MM-DD" or "YYYY-MM-DD HH:mm:ss(.ffffff)" in server time: read both as
  // wall-clock values. A bare "YYYY-MM-DD" would otherwise be parsed as UTC midnight, which is
  // the previous day in any browser west of UTC.
  const text = String(value).replace(' ', 'T').slice(0, 19)
  return new Date(text.length === 10 ? `${text}T00:00:00` : text)
}

/** The server's zone (stored datetimes are wall times in it) and the store's (what users see). */
const serverZone = () => window.server_time_zone || window.time_zone || undefined
const storeZone = () => window.time_zone || undefined

/** Wall-clock parts of the instant `utcMs` in `zone`; `null` for an unknown zone name. */
function partsIn(zone, utcMs) {
  try {
    return Object.fromEntries(
      new Intl.DateTimeFormat('en-US', {
        timeZone: zone,
        hourCycle: 'h23',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
        .formatToParts(new Date(utcMs))
        .filter((p) => p.type !== 'literal')
        .map((p) => [p.type, Number(p.value)]),
    )
  } catch {
    return null
  }
}

const partsUtc = (p) => Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second)

/**
 * A server datetime shown on the store's clock: the same moment, as a local `Date` whose wall
 * time is the store's. Dates without a time, and sites where both zones are the same (every
 * single-store site), are returned as they are.
 */
function toStoreTime(value) {
  const date = toDate(value)
  if (!date || String(value).length <= 10) return date
  const from = serverZone()
  const to = storeZone()
  if (!from || !to || from === to) return date
  const wall = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate(), date.getHours(), date.getMinutes(), date.getSeconds())
  const offset = partsIn(from, wall)
  if (!offset) return date
  const instant = wall - (partsUtc(offset) - wall)
  const p = partsIn(to, instant)
  return p ? new Date(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) : date
}

export function formatDate(value) {
  const date = toStoreTime(value)
  return date ? date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—'
}

export function formatTime(value) {
  const date = toStoreTime(value)
  return date ? date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '—'
}

export function formatDateTime(value) {
  return value ? `${formatDate(value)}, ${formatTime(value)}` : '—'
}

/** Now on `zone`'s clock as plain parts; the browser's clock when the zone is unknown. */
function nowIn(zone) {
  const now = new Date()
  const p = partsIn(zone, now.getTime())
  if (p) return { year: p.year, month: p.month, day: p.day, hour: p.hour, minute: p.minute, second: p.second }
  return {
    year: now.getFullYear(),
    month: now.getMonth() + 1,
    day: now.getDate(),
    hour: now.getHours(),
    minute: now.getMinutes(),
    second: now.getSeconds(),
  }
}

/**
 * The current date and time on the store's clock (`time_zone`): what "today" and "this month"
 * mean for the merchant, not the browser. The site's own zone on single-store sites.
 * @returns {{year: number, month: number, day: number, hour: number, minute: number, second: number}}
 */
export function siteNow() {
  return nowIn(storeZone())
}

/** The current time on the server's clock (`server_time_zone`), the zone stored datetimes use. */
function serverNow() {
  return nowIn(serverZone())
}

const pad2 = (n) => String(n).padStart(2, '0')

/** Now on the server's clock as the server writes it: "YYYY-MM-DD HH:MM:SS". */
export function nowSql() {
  const n = serverNow()
  return `${n.year}-${pad2(n.month)}-${pad2(n.day)} ${pad2(n.hour)}:${pad2(n.minute)}:${pad2(n.second)}`
}

/**
 * Milliseconds from now until a server datetime ("YYYY-MM-DD HH:MM:SS", server time); negative
 * once it has passed. Both are read as wall-clock times on the server's clock, so neither the
 * browser's nor the store's zone matters. `null` for an empty or unreadable value.
 */
export function msUntil(value) {
  const m = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!m) return null
  const [, y, mo, d, h, mi, s] = m.map(Number)
  const n = serverNow()
  return Date.UTC(y, mo - 1, d, h, mi, s || 0) - Date.UTC(n.year, n.month - 1, n.day, n.hour, n.minute, n.second)
}

/** Whether a server datetime ("YYYY-MM-DD HH:MM:SS[.ffffff]", server time) has passed. */
export function isPast(value) {
  return !!value && String(value).slice(0, 19) <= nowSql()
}

/** Whole days from `value` until today on the store's clock (0 = today). */
export function daysAgo(value) {
  const date = toStoreTime(value)
  if (!date) return null
  const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const t = siteNow()
  return Math.round((new Date(t.year, t.month - 1, t.day) - start(date)) / 86400000)
}

export function relativeDay(value) {
  const days = daysAgo(value)
  if (days === null) return 'Never'
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days < 0) return `In ${-days} days`
  return `${days} days ago`
}

/** "1 order", "3 orders". */
export function plural(count, word, many = `${word}s`) {
  return `${formatNumber(count)} ${count === 1 ? word : many}`
}

/** Today's date on the store's clock, "YYYY-MM-DD" (e.g. the Visits page's Today tab). */
export function today() {
  const t = siteNow()
  return `${t.year}-${pad2(t.month)}-${pad2(t.day)}`
}

/** "Draft" for unsubmitted orders, "Cancelled" for cancelled, else the sync state. */
export function orderStatus(order) {
  if (order.docstatus === 0) return 'Draft'
  if (order.docstatus === 2) return 'Cancelled'
  return order.sync_status || 'Pending'
}
