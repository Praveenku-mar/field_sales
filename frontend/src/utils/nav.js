// Sidebar navigation, in the same order and with the same icons as the desk sidebar
// (field_sales/sidebar/field_sales). An item has `route` (an SPA page) or `href` (a desk page).
// `count` names a key returned by frontend_api.get_sidebar_counts.
const MANAGERS = ['System Manager', 'Sales Manager']

export const NAV_ITEMS = [
  { label: 'Home', route: 'Dashboard', icon: 'lucide-house' },
  { label: 'Store Connection', href: '/app/store-connection', icon: 'lucide-plug-zap', roles: ['System Manager'] },
  { label: 'Platform Product', route: 'Products', icon: 'lucide-package' },
  { label: 'Retailer', route: 'Retailers', icon: 'lucide-store', count: 'retailers' },
  { label: 'Sales Rep', route: 'SalesReps', icon: 'lucide-user-round', roles: MANAGERS },
  { label: 'Visit', route: 'Visits', icon: 'lucide-map-pin-check' },
  {
    label: 'Field Order',
    route: 'Orders',
    icon: 'lucide-shopping-cart',
    count: 'failed_orders',
    countTheme: 'red',
    countTooltip: 'Orders that failed to sync',
  },
  { label: 'Sync Log', route: 'SyncLogs', icon: 'lucide-scroll-text', roles: MANAGERS },
  { label: 'Sync Failures', route: 'SyncFailures', icon: 'lucide-triangle-alert' },
]

// Shown under the collapsible "Reports" item.
export const REPORT_LINKS = [
  { label: 'Rep Performance', href: '/app/query-report/Rep Performance', icon: 'lucide-chart-column', roles: MANAGERS },
]
