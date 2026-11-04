<template>
  <div v-if="hasCoords" class="overflow-hidden rounded-xl border border-[#e1e3e5]">
    <iframe :src="src" class="block h-64 w-full border-0" loading="lazy" :title="title" />
    <div class="flex items-center justify-between border-t border-[#e1e3e5] bg-[#fafafa] px-3.5 py-2 text-xs text-polaris-secondary">
      <span>{{ Number(latitude).toFixed(5) }}, {{ Number(longitude).toFixed(5) }}</span>
      <a :href="link" target="_blank" rel="noopener" class="font-medium text-polaris-focus hover:underline">Open map ↗</a>
    </div>
  </div>
  <EmptyState v-else icon="lucide-map-pin-off" title="No location" :description="emptyDescription" />
</template>

<script setup>
import { computed } from 'vue'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  latitude: { type: [Number, String], default: null },
  longitude: { type: [Number, String], default: null },
  title: { type: String, default: 'Map' },
  emptyDescription: { type: String, default: 'No coordinates recorded yet.' },
})

// (0, 0) means "not set", as on the server.
const hasCoords = computed(() => !!(Number(props.latitude) || Number(props.longitude)))
const src = computed(() => {
  const lat = Number(props.latitude)
  const lon = Number(props.longitude)
  const d = 0.004
  const bbox = [lon - d, lat - d, lon + d, lat + d].join(',')
  return `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${lat},${lon}`
})
const link = computed(() => `https://www.openstreetmap.org/?mlat=${props.latitude}&mlon=${props.longitude}#map=17/${props.latitude}/${props.longitude}`)
</script>
