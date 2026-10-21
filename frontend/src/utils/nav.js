// Sidebar navigation. An item has `route` (an SPA page) or `href` (a desk page).
// `count` names a key returned by frontend_api.get_sidebar_counts; `roles` hides the item
// from everyone else (the router and the server check again).
export const MANAGERS = ['System Manager', 'Sales Manager']

export const NAV_ITEMS = [
  { label: 'Dashboard', route: 'Dashboard', icon: 'lucide-layout-dashboard' },
  { label: 'Customers', route: 'Retailers', icon: 'lucide-store', count: 'retailers' },
  { label: 'Visits', route: 'Visits', icon: 'lucide-map-pin-check' },
  {
    label: 'Orders',
    route: 'Orders',
    icon: 'lucide-shopping-cart',
    count: 'failed_orders',
    countTheme: 'red',
    countTooltip: 'Orders that failed to sync',
  },
  { label: 'Products', route: 'Products', icon: 'lucide-package' },
  // Everyone: a rep sees themselves and their team (Sales Rep permission_query_conditions).
  { label: 'Sales Reps', route: 'SalesReps', icon: 'lucide-user-round' },
  // Everyone: a rep sees their own commission; managers also see the team and statements.
  { label: 'Commission', route: 'Commission', icon: 'lucide-wallet' },
  { label: 'Reports', route: 'Reports', icon: 'lucide-chart-column', roles: MANAGERS },
  { label: 'Sync Log', route: 'SyncLogs', icon: 'lucide-scroll-text', roles: MANAGERS },
  { label: 'Sync Failures', route: 'SyncFailures', icon: 'lucide-triangle-alert', roles: MANAGERS },
  { label: 'Settings', route: 'Settings', icon: 'lucide-settings', roles: MANAGERS },
]

// The bottom tab bar on phones (< 768px), where the sidebar is hidden.
export const TAB_ITEMS = [
  { label: 'Dashboard', route: 'Dashboard', icon: 'lucide-layout-dashboard' },
  { label: 'Visits', route: 'Visits', icon: 'lucide-map-pin-check' },
  { label: 'Orders', route: 'Orders', icon: 'lucide-shopping-cart' },
  { label: 'Customers', route: 'Retailers', icon: 'lucide-store' },
]

// Listed on the Reports page.
export const REPORT_LINKS = [
  {
    label: 'Rep Performance',
    description: 'Visits, orders and target achievement per sales rep.',
    route: { name: 'RepPerformance' },
    icon: 'lucide-chart-column',
    roles: MANAGERS,
  },
  {
    label: 'Rep Commission',
    description: 'Sales, earned, pending, reversed, override, paid and unpaid commission per rep.',
    route: { name: 'RepCommission' },
    icon: 'lucide-wallet',
    roles: MANAGERS,
  },
]

// Records with a page in this app (route :name is the docname). Anything else opens in the desk.
const DOCTYPE_ROUTES = {
  'Field Order': 'Order',
  Retailer: 'Retailer',
  Visit: 'Visit',
  'Platform Product': 'Product',
  'Sales Rep': 'SalesRep',
  'Commission Statement': 'CommissionStatement',
  'Sync Log': 'SyncLogEntry',
}

/** Where a notification's document lives: `{ route }` inside the SPA, else `{ href }` in the desk. */
export function documentTarget(doctype, name) {
  if (!doctype || !name) return null
  const routeName = DOCTYPE_ROUTES[doctype]
  if (routeName) return { route: { name: routeName, params: { name } } }
  return { href: `/app/${doctype.toLowerCase().replace(/\s+/g, '-')}/${encodeURIComponent(name)}` }
}
