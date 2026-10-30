<template>
  <div
    v-if="label"
    class="inline-flex items-center gap-1.5 rounded-full bg-[#f1f2f4] border border-[#e1e3e5] py-1 pl-2.5 pr-1.5 text-xs text-polaris-text shadow-sm"
  >
    <span class="lucide-filter size-3.5 text-polaris-secondary" />
    <span>Showing: <strong class="font-semibold text-polaris-text">{{ label }}</strong></span>
    <button
      type="button"
      class="flex size-4.5 items-center justify-center rounded-full text-polaris-secondary transition-colors hover:bg-[#e1e3e5] hover:text-polaris-text"
      title="Clear filter preset"
      @click="clear"
    >
      <span class="lucide-x size-3" />
    </button>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'

defineProps({ label: { type: String, default: null } })

const route = useRoute()
const router = useRouter()

/** Drop ?preset= (and the tab it chose), showing the page's normal list. */
function clear() {
  const query = { ...route.query }
  delete query.preset
  delete query.tab
  router.replace({ query })
}
</script>
