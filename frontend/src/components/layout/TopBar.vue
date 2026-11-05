<template>
  <!-- Polaris Top Bar: On desktop, provides Shopify store context and quick search.
       Inside embedded Shopify admin, the admin carries this top bar already. -->
  <header
    v-if="!embedded"
    class="flex h-topbar shrink-0 items-center justify-between border-b border-[#e1e3e5] bg-white px-4 md:px-6"
  >
    <!-- Left: Store badge on desktop, search icon on mobile -->
    <div class="flex items-center gap-3">
      <!-- Mobile: Search Trigger -->
      <Button
        v-if="!session.isRetailer"
        class="md:hidden"
        variant="ghost"
        icon="lucide-search"
        aria-label="Search"
        @click="$emit('open-search')"
      />
      <!-- Desktop: Shopify Store connection badge -->
      <div class="hidden md:flex items-center gap-2.5">
        <span class="flex size-6 items-center justify-center rounded-md bg-[#008060] text-white shadow-xs">
          <svg class="size-3.5 fill-current" viewBox="0 0 24 24">
            <path d="M19.7 7.7l-2.2-.6c-.1 0-.1-.1-.1-.2 0-1.8-1.5-3.3-3.3-3.3-.4 0-.8.1-1.2.2-.4-1.2-1.5-2.1-2.9-2.1C8.2 1.7 6.8 3.1 6.8 5c0 .2 0 .4.1.6L5 6.2c-.3.1-.5.3-.5.6l-1.9 14c0 .3.1.6.4.8.1.1.3.1.5.1h17c.3 0 .5-.2.5-.5l-1.3-13.5z"/>
          </svg>
        </span>
        <div class="flex items-center gap-2 text-xs">
          <span class="font-semibold text-polaris-text">{{ storeDomain }}</span>
          <span class="inline-flex items-center gap-1 rounded-full bg-[#e4f6ed] px-2 py-0.5 text-[10px] font-semibold text-[#0d5936] border border-[#aee8cb]">
            <span class="size-1.5 rounded-full bg-[#108043] animate-pulse" />
            Live Sync
          </span>
        </div>
      </div>
    </div>

    <!-- Center: Global Search Bar -->
    <div v-if="!session.isRetailer" class="hidden md:flex flex-1 max-w-md mx-6">
      <button
        type="button"
        class="flex w-full items-center justify-between rounded-lg border border-[#e1e3e5] bg-[#f7f7f8] px-3 py-1.5 text-xs text-polaris-secondary transition-colors hover:border-[#c9cccf] hover:bg-white focus:outline-none focus:ring-2 focus:ring-[#005bd3]"
        @click="$emit('open-search')"
      >
        <span class="flex items-center gap-2">
          <span class="lucide-search size-3.5 text-polaris-subdued" />
          <span>Search orders, customers, products...</span>
        </span>
        <kbd class="rounded border border-[#e1e3e5] bg-white px-1.5 py-0.5 text-[10px] font-medium text-polaris-subdued shadow-2xs">Ctrl K</kbd>
      </button>
    </div>

    <!-- Right: Sync status & User Menu -->
    <div class="flex items-center gap-3">
      <RouterLink
        v-if="session.isAdmin"
        :to="{ name: 'SyncFailures' }"
        class="hidden sm:inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-polaris-secondary hover:bg-[#f7f7f8] hover:text-polaris-text transition-colors"
        title="View sync failures"
      >
        <span class="lucide-refresh-cw size-3.5 text-polaris-subdued" />
        <span>Shopify Sync</span>
      </RouterLink>
      <UserMenu collapsed side="bottom" />
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { Button } from 'frappe-ui'
import UserMenu from './UserMenu.vue'
import { isEmbedded } from '@/shopify/embedded'
import { useSessionStore } from '@/stores/session'

const embedded = isEmbedded()
const session = useSessionStore()

const storeDomain = computed(() => {
  if (session.store?.name) {
    return session.store.name.includes('.') ? session.store.name : `${session.store.name}.myshopify.com`
  }
  return 'Shopify Store'
})

defineEmits(['open-search'])
</script>
