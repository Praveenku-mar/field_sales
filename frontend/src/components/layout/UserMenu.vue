<template>
  <!-- The signed-in user, with Switch to Desk and Log out. Sidebar footer on desktop;
       `collapsed` (sidebar collapsed, or the phone top bar) shows the avatar only. -->
  <Dropdown :options="options" side="top" align="start" :match-trigger-width="!collapsed">
    <button
      type="button"
      class="flex h-9 items-center gap-2 rounded-4 px-1 text-left hover:bg-surface-gray-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-3"
      :class="collapsed ? 'w-auto' : 'w-full'"
      :aria-label="`Account: ${session.fullName}`"
    >
      <Avatar :label="session.fullName" :image="session.userImage" size="md" />
      <template v-if="!collapsed">
        <span class="min-w-0 flex-1 truncate text-base font-medium text-ink-gray-8">{{ session.fullName }}</span>
        <span class="lucide-chevron-up size-4 shrink-0 text-ink-gray-5" aria-hidden="true" />
      </template>
    </button>
  </Dropdown>
</template>

<script setup>
import { computed } from 'vue'
import { Avatar, Dropdown } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'

defineProps({
  collapsed: { type: Boolean, default: false },
})

const session = useSessionStore()

const options = computed(() => [
  {
    group: session.user,
    options: [
      { label: 'Switch to Desk', icon: 'lucide-app-window', onClick: () => (window.location.href = '/app') },
      { label: 'Log out', icon: 'lucide-log-out', onClick: () => session.logout() },
    ],
  },
])
</script>
