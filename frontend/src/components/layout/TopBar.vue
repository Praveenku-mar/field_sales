<template>
  <header class="flex h-topbar shrink-0 items-center gap-3 border-b border-desk-border bg-white px-4">
    <Breadcrumbs :items="crumbs" class="min-w-0 flex-1" />

    <Dropdown :options="userMenu" align="end">
      <button class="flex items-center rounded-full focus:outline-none focus-visible:ring-2">
        <Avatar :label="session.fullName" :image="session.userImage" size="md" />
      </button>
    </Dropdown>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Avatar, Breadcrumbs, Dropdown } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const crumbs = computed(() => {
  const items = []
  if (route.meta.parent) {
    const parent = router.resolve({ name: route.meta.parent })
    items.push({ label: parent.meta.title, route: { name: route.meta.parent } })
  }
  items.push({ label: route.meta.title || route.params.name || '', route: route.fullPath })
  return items
})

const userMenu = computed(() => [
  {
    group: session.fullName,
    options: [
      { label: 'Switch to Desk', icon: 'lucide-app-window', onClick: () => (window.location.href = '/app') },
      { label: 'Logout', icon: 'lucide-log-out', onClick: () => session.logout() },
    ],
  },
])
</script>
