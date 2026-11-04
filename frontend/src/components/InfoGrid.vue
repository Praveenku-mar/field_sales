<template>
  <dl class="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2" :class="columns === 3 && 'lg:grid-cols-3'">
    <div v-for="item in visibleItems" :key="item.label" class="min-w-0">
      <dt class="text-xs font-medium text-polaris-secondary">{{ item.label }}</dt>
      <dd class="mt-0.5 break-words text-sm font-medium text-polaris-text">
        <a v-if="item.href" :href="item.href" class="text-polaris-focus hover:underline">{{ item.value }}</a>
        <RouterLink v-else-if="item.route" :to="item.route" class="text-polaris-focus hover:underline">{{ item.value }}</RouterLink>
        <template v-else>{{ item.value ?? '—' }}</template>
      </dd>
    </div>
  </dl>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /** [{ label, value, href?, route?, hide? }] */
  items: { type: Array, required: true },
  columns: { type: Number, default: 2 },
})
const visibleItems = computed(() => props.items.filter((item) => !item.hide))
</script>
