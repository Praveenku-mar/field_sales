<template>
  <!-- Phones have no sidebar: search and the user menu live here. Desktop
       shows no top bar — each page's heading already names it, so a duplicate
       title strip would only repeat it. -->
  <header class="flex h-topbar shrink-0 items-center gap-3 border-b border-outline-gray-2 bg-surface-base px-4 md:hidden">
    <Button variant="ghost" icon="lucide-search" aria-label="Search" @click="$emit('open-search')" />
    <UserMenu collapsed />
  </header>
</template>

<script setup>
import { Button } from 'frappe-ui'
import UserMenu from './UserMenu.vue'

defineEmits(['open-search'])
</script>
