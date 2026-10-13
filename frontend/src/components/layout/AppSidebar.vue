<template>
  <Sidebar v-model:collapsed="collapsed" width="224px" class="border-r border-desk-border">
    <SidebarHeader
      title="Field Sales"
      logo="/assets/field_sales/images/logo.svg"
      :menu-items="appMenu"
      class="px-2"
    />

    <div class="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-2">
      <SidebarItem label="Search" icon="lucide-search" @click="$emit('open-search')">
        <template #suffix>
          <kbd class="mr-2 text-xs text-ink-gray-4">Ctrl K</kbd>
        </template>
      </SidebarItem>
      <SidebarItem label="Notification" icon="lucide-bell" href="/app/notification-log" target="_blank" />

      <div class="h-4 shrink-0" />

      <SidebarItem
        v-for="item in navItems"
        :key="item.label"
        :label="item.label"
        :icon="item.icon"
        :route="item.route && { name: item.route }"
        :href="item.href"
        :active="isActive(item)"
      >
        <template v-if="countOf(item)" #suffix>
          <Tooltip :text="item.countTooltip" :disabled="!item.countTooltip">
            <span
              class="mr-2 text-xs tabular-nums"
              :class="item.countTheme === 'red' ? 'font-medium text-ink-red-5' : 'text-ink-gray-5'"
            >
              {{ countOf(item) }}
            </span>
          </Tooltip>
        </template>
      </SidebarItem>

      <!-- Reports: a collapsible group of desk pages, as in the desk sidebar -->
      <template v-if="reportLinks.length">
        <SidebarItem
          label="Reports"
          icon="lucide-notepad-text"
          :aria-expanded="reportsOpen"
          @click="reportsOpen = !reportsOpen"
        >
          <template #suffix>
            <span
              class="lucide-chevron-down mr-2 size-4 text-ink-gray-5 transition-transform"
              :class="!reportsOpen && '-rotate-90'"
            />
          </template>
        </SidebarItem>
        <div
          v-show="reportsOpen"
          class="flex flex-col gap-0.5"
          :class="!collapsed && 'ml-4 border-l border-outline-gray-2 pl-2'"
        >
          <SidebarItem
            v-for="link in reportLinks"
            :key="link.href"
            :label="link.label"
            :icon="collapsed ? link.icon : undefined"
            :href="link.href"
          />
        </div>
      </template>
    </div>

    <div class="px-2 pb-2">
      <SidebarCollapseToggle />
    </div>
  </Sidebar>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Sidebar, SidebarCollapseToggle, SidebarHeader, SidebarItem, Tooltip, createResource } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'
import { NAV_ITEMS, REPORT_LINKS } from '@/utils/nav'

defineEmits(['open-search'])

const session = useSessionStore()
const route = useRoute()

const allowed = (item) => !item.roles || session.hasRole(...item.roles)
const navItems = computed(() => NAV_ITEMS.filter(allowed))
const reportLinks = computed(() => REPORT_LINKS.filter(allowed))

const appMenu = computed(() => [
  {
    group: session.store.name || 'No store connected',
    options: [
      { label: 'Switch to Desk', icon: 'lucide-app-window', onClick: () => (window.location.href = '/app') },
      { label: 'Logout', icon: 'lucide-log-out', onClick: () => session.logout() },
    ],
  },
])

const counts = createResource({
  url: 'field_sales.frontend_api.get_sidebar_counts',
  auto: true,
})

function countOf(item) {
  return item.count ? counts.data?.[item.count] || 0 : 0
}

function isActive(item) {
  // Detail pages (e.g. /retailers/RET-0001) keep their list's item highlighted.
  return !!item.route && (route.name === item.route || route.meta.parent === item.route)
}

// Collapsed state and the Reports group survive reloads; they are per-browser preferences.
const collapsed = usePreference('fs:sidebar-collapsed', false)
const reportsOpen = usePreference('fs:sidebar-reports-open', true)

function usePreference(key, fallback) {
  let stored = null
  try {
    stored = localStorage.getItem(key)
  } catch {}
  const value = ref(stored === null ? fallback : stored === '1')
  watch(value, (v) => {
    try {
      localStorage.setItem(key, v ? '1' : '0')
    } catch {}
  })
  return value
}

// Counts change as people work; refresh them when moving between pages.
watch(() => route.name, () => counts.reload())
</script>
