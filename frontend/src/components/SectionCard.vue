<template>
  <section class="fs-card overflow-hidden">
    <header
      v-if="title || $slots.actions"
      class="flex items-center justify-between gap-3 border-b border-[#f1f2f4] px-4 sm:px-5 py-3"
    >
      <h2 class="text-sm font-semibold text-polaris-text">{{ title }}</h2>
      <div v-if="$slots.actions" class="flex items-center gap-2"><slot name="actions" /></div>
    </header>
    <div :class="flush ? '' : 'p-4 sm:p-5'"><slot /></div>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: null },
  /** No inner padding (for tables and lists that draw their own). */
  flush: { type: Boolean, default: false },
})
</script>
