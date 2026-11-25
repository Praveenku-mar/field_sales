<template>
  <!-- Vendor staff on the shared site: pick the store to work in for this session. -->
  <div class="fs-card w-full max-w-lg p-6">
    <h1 class="text-base font-semibold text-polaris-text">Choose a store</h1>
    <p class="mt-1 text-sm text-polaris-secondary">
      You work in one store at a time. Each switch is recorded on the store.
    </p>
    <TextInput v-model="search" class="mt-4" placeholder="Search by shop or code" :debounce="150" />

    <div v-if="stores.loading && !stores.data" class="flex justify-center py-10 text-ink-gray-5">
      <LoadingIndicator class="size-5" />
    </div>
    <div v-else-if="stores.error" class="mt-4 text-sm text-ink-red-4">{{ errorText }}</div>
    <ul v-else class="mt-4 max-h-96 divide-y divide-[#f1f2f4] overflow-y-auto rounded-lg border border-[#e1e3e5]">
      <li v-for="s in shown" :key="s.name">
        <button
          type="button"
          class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-[#f7f7f7] disabled:opacity-60"
          :disabled="!!switching"
          @click="pick(s.name)"
        >
          <span class="min-w-0">
            <span class="block truncate text-sm font-medium text-polaris-text">{{ s.shop_name || s.name }}</span>
            <span class="block truncate text-xs text-polaris-secondary">{{ s.name }} · {{ s.store_url || 'not connected' }}</span>
          </span>
          <span v-if="s.uninstalled_on" class="shrink-0 text-xs text-polaris-secondary">Uninstalled</span>
          <span v-else-if="!s.enabled" class="shrink-0 text-xs text-polaris-secondary">Disabled</span>
          <LoadingIndicator v-if="switching === s.name" class="size-4 shrink-0" />
        </button>
      </li>
      <li v-if="!shown.length" class="px-4 py-6 text-center text-sm text-polaris-secondary">No store matches.</li>
    </ul>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { LoadingIndicator, TextInput, createResource } from 'frappe-ui'
import { callMethod } from '@/utils/api'

const search = ref('')
const switching = ref(null)

// The server searches (and caps the list), so a site with many merchants stays quick to open.
const stores = createResource({
  url: 'field_sales.frontend_api.get_vendor_stores',
  method: 'GET',
  makeParams: () => ({ txt: search.value.trim() }),
  auto: true,
})

// TextInput already debounces the typing.
watch(search, () => stores.reload())

const errorText = computed(() => String(stores.error?.messages?.[0] || stores.error || ''))

const shown = computed(() => stores.data || [])

async function pick(store) {
  switching.value = store
  try {
    await callMethod('field_sales.frontend_api.switch_store', { store })
    // Every page, the boot values and the session follow the store: start again in it.
    window.location.reload()
  } catch {
    switching.value = null // callMethod showed the error
  }
}
</script>
