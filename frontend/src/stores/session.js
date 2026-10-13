import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { createResource } from 'frappe-ui'

// Roles that may open the app at all (checked again on the server).
export const APP_ROLES = ['System Manager', 'Sales Manager', 'Field Sales Rep']

export function redirectToLogin() {
  const target = window.location.pathname + window.location.search
  window.location.href = `/login?redirect-to=${encodeURIComponent(target || '/fieldsales')}`
}

/** Frappe sets this cookie on every response; "Guest" (or missing) means not signed in. */
function cookieUser() {
  const match = document.cookie.match(/(?:^|;\s*)user_id=([^;]*)/)
  const user = match ? decodeURIComponent(match[1]) : null
  return user && user !== 'Guest' ? user : null
}

/** Signed out or session expired: the only errors that the login page can fix. */
function isAuthError(error) {
  return error?.status === 401 || ['AuthenticationError', 'SessionExpired'].includes(error?.exc_type)
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

  function hasRole(...wanted) {
    return wanted.some((role) => roles.value.includes(role))
  }

  /**
   * Load the session once. Guests go to the login page. A signed-in user without
   * an app role, or any other failure, gets a message instead: sending them to
   * login would bounce straight back here.
   */
  async function load() {
    if (ready.value) return !!user.value
    if (!cookieUser()) {
      redirectToLogin()
      return false
    }
    try {
      await resource.fetch()
      error.value = null
    } catch (e) {
      if (isAuthError(e)) {
        redirectToLogin()
        return false
      }
      const noAccess = e?.status === 403 || e?.exc_type === 'PermissionError'
      error.value = {
        noAccess,
        title: noAccess ? 'No access to Field Sales' : 'Could not load Field Sales',
        message: noAccess
          ? 'Your account needs the Field Sales Rep, Sales Manager or System Manager role.'
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
        window.location.href = '/login'
      })
  }

  return {
    ready,
    error,
    user,
    fullName,
    userImage,
    roles,
    salesRep,
    store,
    isAdmin,
    isManager,
    hasRole,
    load,
    retry,
    logout,
  }
})
