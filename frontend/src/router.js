import { createRouter, createWebHistory } from 'vue-router'
import { toast } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'

// Sync Log and the Sales Rep list are manager pages, as in the desk.
const MANAGERS = ['System Manager', 'Sales Manager']

// meta.title: page title and last breadcrumb; meta.parent: breadcrumb parent route name.
const routes = [
  { path: '/', redirect: { name: 'Dashboard' } },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/pages/Dashboard.vue'),
    meta: { title: 'Dashboard' },
  },
  {
    path: '/retailers',
    name: 'Retailers',
    component: () => import('@/pages/Retailers.vue'),
    meta: { title: 'Retailers' },
  },
  {
    path: '/retailers/:name',
    name: 'Retailer',
    component: () => import('@/pages/RetailerDetail.vue'),
    props: true,
    meta: { parent: 'Retailers' },
  },
  {
    path: '/visits',
    name: 'Visits',
    component: () => import('@/pages/Visits.vue'),
    meta: { title: 'Visits' },
  },
  {
    path: '/visits/:name',
    name: 'Visit',
    component: () => import('@/pages/VisitDetail.vue'),
    props: true,
    meta: { parent: 'Visits' },
  },
  {
    path: '/orders',
    name: 'Orders',
    component: () => import('@/pages/Orders.vue'),
    meta: { title: 'Field Orders' },
  },
  {
    path: '/orders/:name',
    name: 'Order',
    component: () => import('@/pages/OrderDetail.vue'),
    props: true,
    meta: { parent: 'Orders' },
  },
  {
    path: '/products',
    name: 'Products',
    component: () => import('@/pages/Products.vue'),
    meta: { title: 'Products' },
  },
  {
    path: '/products/:name',
    name: 'Product',
    component: () => import('@/pages/ProductDetail.vue'),
    props: true,
    meta: { parent: 'Products' },
  },
  {
    path: '/sales-reps',
    name: 'SalesReps',
    component: () => import('@/pages/SalesReps.vue'),
    meta: { title: 'Sales Reps', roles: MANAGERS },
  },
  {
    // A rep can open their own page (and their team's): the server limits which reps load.
    path: '/sales-reps/:name',
    name: 'SalesRep',
    component: () => import('@/pages/SalesRepDetail.vue'),
    props: true,
    meta: { parent: 'SalesReps' },
  },
  {
    path: '/sync-log',
    name: 'SyncLogs',
    component: () => import('@/pages/SyncLogs.vue'),
    meta: { title: 'Sync Log', roles: MANAGERS },
  },
  {
    path: '/sync-log/:name',
    name: 'SyncLogEntry',
    component: () => import('@/pages/SyncLogDetail.vue'),
    props: true,
    meta: { parent: 'SyncLogs', roles: MANAGERS },
  },
  {
    path: '/sync-failures',
    name: 'SyncFailures',
    component: () => import('@/pages/SyncFailures.vue'),
    meta: { title: 'Sync Failures' },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/pages/Settings.vue'),
    meta: { title: 'Settings', roles: ['System Manager'] },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/pages/NotFound.vue'),
    meta: { title: 'Not Found' },
  },
]

const router = createRouter({
  history: createWebHistory('/fieldsales'),
  routes,
})

router.beforeEach(async (to) => {
  const session = useSessionStore()
  if (!(await session.load())) return false

  if (to.meta.roles && !session.hasRole(...to.meta.roles)) {
    toast.error('You do not have access to ' + (to.meta.title || 'this page'))
    return { name: 'Dashboard' }
  }
})

router.afterEach((to) => {
  const title = to.meta.title || to.params.name
  document.title = title ? `${title} · Field Sales` : 'Field Sales'
})

export default router
