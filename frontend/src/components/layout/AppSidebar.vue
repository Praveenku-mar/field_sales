<template>
  <Sidebar v-model:collapsed="collapsed" width="240px" class="border-r border-[#e1e3e5] bg-white">
    <SidebarHeader
      title="Field Sales"
      :subtitle="session.store.name || 'No store connected'"
      logo="/assets/field_sales/images/logo.svg"
      class="px-2"
    />

    <div class="flex flex-1 flex-col gap-1 overflow-y-auto px-2 pb-2">
      <!-- Staff tools: a customer login has no search or notifications. -->
      <SidebarItem
        v-if="!session.isRetailer"
        label="Search"
        icon="lucide-search"
        style="height: 2.25rem"
        @click="$emit('open-search')"
      >
        <template #prefix><span class="lucide-search size-5 text-polaris-secondary" /></template>
        <template #suffix>
          <kbd class="mr-2 rounded border border-[#e1e3e5] bg-[#f1f2f4] px-1.5 py-0.5 text-[11px] font-medium text-polaris-subdued">Ctrl K</kbd>
        </template>
      </SidebarItem>
      <SidebarItem
        v-if="!session.isRetailer"
        label="Notification"
        icon="lucide-bell"
        style="height: 2.25rem"
        :route="{ name: 'Notifications' }"
        :active="route.name === 'Notifications'"
      >
        <template #prefix><span class="lucide-bell size-5 text-polaris-secondary" /></template>
        <template v-if="counts.data?.unread_notifications" #suffix>
          <span class="mr-2 inline-flex items-center rounded-full bg-[#fde8e8] px-1.5 py-0.2 text-xs font-semibold tabular-nums text-[#9b1c1c]">
            {{ counts.data.unread_notifications }}
          </span>
        </template>
      </SidebarItem>

      <div v-if="!session.isRetailer" class="my-1 border-t border-[#f1f2f4]" />

      <SidebarItem
        v-for="item in navItems"
        :key="item.label"
        :label="item.label"
        style="height: 2.25rem"
        :route="item.route && { name: item.route }"
        :href="item.href"
        :active="isActive(item)"
      >
        <template #prefix><span :class="[item.icon, 'size-5 text-polaris-secondary']" /></template>
        <span class="truncate text-sm font-medium leading-tighter">{{ item.label }}</span>
        <template v-if="countOf(item)" #suffix>
          <Tooltip :text="item.countTooltip" :disabled="!item.countTooltip">
            <span
              class="mr-2 inline-flex items-center rounded-full px-1.5 py-0.2 text-xs tabular-nums"
              :class="item.countTheme === 'red' ? 'bg-[#fde8e8] font-semibold text-[#9b1c1c]' : 'bg-[#f1f2f4] text-polaris-secondary font-medium'"
            >
              {{ countOf(item) }}
            </span>
          </Tooltip>
        </template>
      </SidebarItem>
    </div>

    <div class="flex flex-col gap-0.5 border-t border-[#e1e3e5] p-2">
      <UserMenu :collapsed="collapsed" />
      <SidebarCollapseToggle />
    </div>
  </Sidebar>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Sidebar, SidebarCollapseToggle, SidebarHeader, SidebarItem, Tooltip, createResource } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'
import { NAV_ITEMS } from '@/utils/nav'
import { getPreference, setPreference } from '@/utils/storage'
import UserMenu from './UserMenu.vue'

defineEmits(['open-search'])

const session = useSessionStore()
const route = useRoute()

const navItems = computed(() => NAV_ITEMS.filter((item) => session.canSee(item)))

// A customer login shows no counts (its nav has none), so it never fetches or polls them.
const counts = createResource({
  url: 'field_sales.frontend_api.get_sidebar_counts',
  auto: !session.isRetailer,
})

function countOf(item) {
  return item.count ? counts.data?.[item.count] || 0 : 0
}

function isActive(item) {
  // Detail pages (e.g. /retailers/RET-0001) keep their list's item highlighted.
  return !!item.route && (route.name === item.route || route.meta.parent === item.route)
}

// Collapsed state survives reloads; it is a per-browser preference.
const collapsed = ref(getPreference('fs:sidebar-collapsed') === '1')
watch(collapsed, (v) => setPreference('fs:sidebar-collapsed', v ? '1' : '0'))

// Counts change as people work; refresh them when moving between pages.
watch(
  () => route.name,
  () => !session.isRetailer && counts.reload(),
)
// The Notifications page announces read/unread changes made without leaving it.
const reloadCounts = () => counts.reload()
// There is no realtime connection, so poll while the tab is in view to pick up new notifications.
let poll = null
onMounted(() => {
  window.addEventListener('fs:notifications-changed', reloadCounts)
  poll = setInterval(() => {
    if (!session.isRetailer && document.visibilityState === 'visible') counts.reload()
  }, 60000)
})
onBeforeUnmount(() => {
  window.removeEventListener('fs:notifications-changed', reloadCounts)
  clearInterval(poll)
})
</script>
