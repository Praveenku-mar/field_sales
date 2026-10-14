<template>
  <Sidebar v-model:collapsed="collapsed" width="224px" class="border-r border-outline-gray-2">
    <SidebarHeader
      title="Field Sales"
      :subtitle="session.store.name || 'No store connected'"
      logo="/assets/field_sales/images/logo.svg"
      class="px-2"
    />

    <div class="flex flex-1 flex-col gap-1.5 overflow-y-auto px-2 pb-2">
      <SidebarItem label="Search" icon="lucide-search" style="height: 2.5rem" @click="$emit('open-search')">
        <template #prefix><span class="lucide-search size-6 text-ink-gray-6" /></template>
        <template #suffix>
          <kbd class="mr-2 text-xs text-ink-gray-4">Ctrl K</kbd>
        </template>
      </SidebarItem>
      <SidebarItem label="Notification" icon="lucide-bell" style="height: 2.5rem" href="/app/notification-log" target="_blank">
        <template #prefix><span class="lucide-bell size-6 text-ink-gray-6" /></template>
      </SidebarItem>

      <div class="h-6 shrink-0" />

      <SidebarItem
        v-for="item in navItems"
        :key="item.label"
        :label="item.label"
        style="height: 2.5rem"
        :route="item.route && { name: item.route }"
        :href="item.href"
        :active="isActive(item)"
      >
        <template #prefix><span :class="[item.icon, 'size-6 text-ink-gray-6']" /></template>
        <span class="truncate text-base leading-tighter">{{ item.label }}</span>
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
    </div>

    <div class="flex flex-col gap-0.5 border-t border-outline-gray-2 p-2">
      <UserMenu :collapsed="collapsed" />
      <SidebarCollapseToggle />
    </div>
  </Sidebar>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Sidebar, SidebarCollapseToggle, SidebarHeader, SidebarItem, Tooltip, createResource } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'
import { NAV_ITEMS } from '@/utils/nav'
import UserMenu from './UserMenu.vue'

defineEmits(['open-search'])

const session = useSessionStore()
const route = useRoute()

const navItems = computed(() => NAV_ITEMS.filter((item) => !item.roles || session.hasRole(...item.roles)))

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

// Collapsed state survives reloads; it is a per-browser preference.
let stored = null
try {
  stored = localStorage.getItem('fs:sidebar-collapsed')
} catch {}
const collapsed = ref(stored === '1')
watch(collapsed, (v) => {
  try {
    localStorage.setItem('fs:sidebar-collapsed', v ? '1' : '0')
  } catch {}
})

// Counts change as people work; refresh them when moving between pages.
watch(() => route.name, () => counts.reload())
</script>
