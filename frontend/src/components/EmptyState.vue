<template>
  <div class="flex flex-col items-center justify-center px-6 py-12 sm:py-16 text-center">
    <div class="flex size-12 items-center justify-center rounded-full bg-[#f1f2f4] border border-[#e1e3e5]">
      <span :class="[icon, 'size-6 text-[#616161]']" />
    </div>
    <div class="mt-3.5 text-base font-semibold text-polaris-text">{{ title }}</div>
    <div v-if="description" class="mt-1.5 max-w-sm text-sm text-polaris-secondary leading-relaxed">{{ description }}</div>
    <div v-if="$slots.default" class="mt-5 flex items-center justify-center gap-2.5">
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: null },
  icon: { type: String, default: 'lucide-inbox' },
})
</script>
