<template>
  <!-- Phones (< 768px): the sidebar is hidden and these four pages sit in a bottom tab bar. -->
  <nav
    aria-label="Main"
    class="grid shrink-0 border-t border-[#e1e3e5] bg-white pb-[env(safe-area-inset-bottom)] md:hidden shadow-sm"
    :style="{ gridTemplateColumns: `repeat(${Math.max(items.length, 1)}, minmax(0, 1fr))` }"
  >
    <RouterLink
      v-for="item in items"
      :key="item.route"
      :to="{ name: item.route }"
      class="flex min-h-[52px] flex-col items-center justify-center gap-1 text-xs transition-colors focus:outline-none"
      :class="isActive(item) ? 'font-semibold text-polaris-focus' : 'text-polaris-secondary hover:text-polaris-text'"
      :aria-current="isActive(item) ? 'page' : undefined"
    >
      <span :class="item.icon" class="size-5" aria-hidden="true" />
      <span>{{ item.label }}</span>
    </RouterLink>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { TAB_ITEMS } from '@/utils/nav'

const route = useRoute()
const session = useSessionStore()

// Staff get their four pages; a customer login gets its My Orders tab.
const items = computed(() => TAB_ITEMS.filter((item) => session.canSee(item)))

function isActive(item) {
  return route.name === item.route || route.meta.parent === item.route
}
</script>
