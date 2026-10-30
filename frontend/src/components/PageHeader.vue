<template>
  <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 px-4 sm:px-6 pt-5 pb-4">
    <div class="flex min-w-0 items-center gap-3">
      <h1 class="truncate text-xl sm:text-2xl font-bold tracking-tight text-polaris-text">{{ title }}</h1>
      <StatusPill v-if="status" :status="status" />
      <slot name="meta" />
    </div>
    <div v-if="$slots.actions" class="flex shrink-0 flex-wrap items-center gap-2.5">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup>
import StatusPill from './StatusPill.vue'

defineProps({
  title: { type: String, required: true },
  /** Shown as a StatusPill next to the title. */
  status: { type: String, default: null },
})
</script>
