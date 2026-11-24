import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { createResource } from 'frappe-ui'
import { isEmbedded } from '@/shopify/embedded'
import { APP_BASE } from '@/utils/base'

// Roles that may open the app at all (checked again on the server). Field Sales
// Retailer is the customer login: it reaches My Orders only (see the router guard).
export const APP_ROLES = ['System Manager', 'Store Admin', 'Sales Manager', 'Field Sales Rep', 'Field Sales Retailer']

export function redirectToLogin() {
  const target = window.location.pathname + window.location.search
  window.location.href = `/login?redirect-to=${encodeURIComponent(
    target || window.default_route || APP_BASE,
  )}`
}

/** Frappe sets this cookie on every response; "Guest" (or missing) means not signed in. */
export function cookieUser() {
  const match = document.cookie.match(/(?:^|;\s*)user_id=([^;]*)/)
  const user = match ? decodeURIComponent(match[1]) : null
  return user && user !== 'Guest' ? user : null
}

/** Signed out or session expired: the only errors that the login page can fix. */
function isAuthError(error) {
  return error?.status === 401 || ['AuthenticationError', 'SessionExpired'].includes(error?.exc_type)
}

/**
 * The session's store values win over the page's boot values (www/fieldsales.py), which
 * utils/format.js and the Settings forms read from `window`. Inside the Shopify admin the page
 * itself loads without a sign-in, so its boot cannot know the store and falls back to the site's
 * currency and zone (wrong on a shared site with several stores); the session API is signed in
 * (session token or cookie) and answers for the user's store. Missing values keep the boot's.
 */
function applySessionStore(session) {
  if (session.currency) window.currency = session.currency
  if (session.time_zone) window.time_zone = session.time_zone
  if (session.server_time_zone) window.server_time_zone = session.server_time_zone
  if (session.store?.code) window.store = session.store.code
}

export const useSessionStore = defineStore('session', () => {
  const ready = ref(false)
  /** Why the app cannot start: { title, message, noAccess } — shown by App.vue instead of the shell. */
  const error = ref(null)

  const resource = createResource({
    url: 'field_sales.frontend_api.get_session',
  })

  const data = computed(() => resource.data || {})
  const user = computed(() => data.value.user || null)
  const fullName = computed(() => data.value.full_name || user.value)
  const userImage = computed(() => data.value.user_image)
  const roles = computed(() => data.value.roles || [])
  const salesRep = computed(() => data.value.sales_rep || null)
  const store = computed(() => data.value.store || {})
  const isAdmin = computed(() => !!data.value.is_admin)
  const isManager = computed(() => !!data.value.is_manager)
  // A customer login: sees My Orders only (the server scopes every list to its retailer).
  const isRetailer = computed(() => !!data.value.is_retailer)
  const retailer = computed(() => data.value.retailer || null)
  // Settings → Commission; off for companies that pay no commission (missing = on).
  const commissionEnabled = computed(() => data.value.commission_enabled !== false)
  // Vendor staff on the shared site: they work in one store at a time (VendorStorePicker).
  const isVendor = computed(() => !!data.value.is_vendor)
  const needsStore = computed(() => isVendor.value && !store.value.code)
  // Shared site, right after a Shopify install: the merchant's admin email comes first, for the
  // store's own Store Admin only (the install's Shopify sign-in). Vendor staff and the store's
  // other users carry on: they must not name the merchant's admin for them.
  const needsAdminSetup = computed(
    () => !!store.value.needs_admin_setup && !isVendor.value && roles.value.includes('Store Admin'),
  )

  function hasRole(...wanted) {
    return wanted.some((role) => roles.value.includes(role))
  }

  /** Nav items, report links and routes: `roles` limits who sees them, `feature: 'commission'`
   * hides them when it is off, `retailerOnly` shows them to a customer login alone — a role
   * check is not enough there (e.g. Administrator holds every role). */
  function canSee(item) {
    if (item.retailerOnly) return isRetailer.value
    if (item.roles && !hasRole(...item.roles)) return false
    if (item.feature === 'commission' && !commissionEnabled.value) return false
    return true
  }

  /** Re-read the session after a setting it carries changes (e.g. commission turned on or off). */
  function reload() {
    return resource.reload().then((result) => {
      applySessionStore(data.value)
      return result
    })
  }

  /**
   * Load the session once. Guests go to the login page. A signed-in user without
   * an app role, or any other failure, gets a message instead: sending them to
   * login would bounce straight back here.
   */
  async function load() {
    if (ready.value) return !!user.value
    // Inside the Shopify admin the session token signs requests in; there is no login page
    // to go to (it can't set cookies in the admin's iframe), so failures show a message.
    const embedded = isEmbedded()
    if (!embedded && !cookieUser()) {
      redirectToLogin()
      return false
    }
    try {
      await resource.fetch()
      applySessionStore(data.value)
      error.value = null
    } catch (e) {
      if (isAuthError(e) && !embedded) {
        redirectToLogin()
        return false
      }
      const noAccess = e?.status === 403 || e?.exc_type === 'PermissionError'
      error.value = {
        noAccess,
        embedded,
        title: noAccess ? 'No access to Agamsale' : 'Could not load Agamsale',
        message: noAccess
          ? e?.messages?.[0] || 'Your account needs a Field Sales role.'
          : e?.messages?.[0] || e?.message || 'Unknown error',
      }
    }
    ready.value = true
    return !!user.value
  }

  /** Retry after an error (App.vue's Retry button). */
  function retry() {
    ready.value = false
    error.value = null
    return load()
  }

  function logout() {
    return createResource({ url: 'logout' })
      .fetch()
      .finally(() => {
        window.location.href = `/login?redirect-to=${encodeURIComponent(APP_BASE)}`
      })
  }

  return {
    ready,
    error,
    user,
    isVendor,
    needsStore,
    needsAdminSetup,
    fullName,
    userImage,
    roles,
    salesRep,
    store,
    isAdmin,
    isManager,
    isRetailer,
    retailer,
    commissionEnabled,
    hasRole,
    canSee,
    reload,
    load,
    retry,
    logout,
  }
})
