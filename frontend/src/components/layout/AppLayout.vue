<template>
  <div class="flex h-full overflow-hidden bg-desk-bg">
    <AppSidebar class="hidden md:flex" @open-search="searchOpen = true" />
    <div class="flex min-w-0 flex-1 flex-col">
      <TopBar @open-search="searchOpen = true" />
      <main class="flex-1 overflow-y-auto">
        <slot />
      </main>
      <MobileTabBar />
    </div>
    <CommandPalette v-model="searchOpen" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppSidebar from './AppSidebar.vue'
import TopBar from './TopBar.vue'
import MobileTabBar from './MobileTabBar.vue'
import CommandPalette from './CommandPalette.vue'

const searchOpen = ref(false)

// Ctrl+K / Cmd+K opens the command palette from anywhere.
function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    searchOpen.value = !searchOpen.value
  }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>
