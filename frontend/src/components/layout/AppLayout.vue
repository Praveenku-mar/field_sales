<template>
  <div class="flex h-full overflow-hidden bg-desk-bg">
    <AppSidebar @open-search="searchOpen = true" />
    <div class="flex min-w-0 flex-1 flex-col">
      <TopBar />
      <main class="flex-1 overflow-y-auto">
        <slot />
      </main>
    </div>
    <CommandPalette v-model="searchOpen" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppSidebar from './AppSidebar.vue'
import TopBar from './TopBar.vue'
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
