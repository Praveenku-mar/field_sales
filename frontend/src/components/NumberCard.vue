<template>
  <component
    :is="to ? 'RouterLink' : 'div'"
    :to="to"
    class="fs-card block p-4 sm:p-5"
    :class="to && 'fs-card-hover group cursor-pointer'"
  >
    <div class="flex items-center justify-between text-polaris-secondary">
      <span class="truncate text-xs font-semibold uppercase tracking-wider">{{ label }}</span>
      <span v-if="icon" :class="[icon, 'size-4 text-polaris-subdued transition-colors group-hover:text-polaris-text']" />
    </div>
    <div class="mt-2.5 flex items-baseline gap-2.5">
      <span v-if="loading" class="h-8 w-20 animate-pulse rounded bg-[#ebebeb]" />
      <span v-else class="text-2xl sm:text-3xl font-bold tracking-tight tabular-nums" :class="valueClass">{{ formatted }}</span>
      <span
        v-if="delta != null && !loading"
        class="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-semibold"
        :class="delta >= 0 ? 'bg-[#e4f6ed] text-[#0d5936]' : 'bg-[#fde8e8] text-[#9b1c1c]'"
      >
        <span>{{ delta >= 0 ? '↑' : '↓' }}</span>
        <span>{{ Math.abs(delta).toFixed(1) }}%</span>
      </span>
      <slot v-if="!loading" name="badge" />
    </div>
    <div v-if="caption" class="mt-1.5 truncate text-xs text-polaris-secondary">{{ caption }}</div>
  </component>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [Number, String], default: null },
  /** 'number' | 'currency' | 'percent' */
  format: { type: String, default: 'number' },
  currency: { type: String, default: null },
  icon: { type: String, default: null },
  caption: { type: String, default: null },
  /** Change vs a comparison period, in percent. */
  delta: { type: Number, default: null },
  theme: { type: String, default: null },
  loading: { type: Boolean, default: false },
  to: { type: [String, Object], default: null },
})

const formatted = computed(() => {
  if (props.value == null || props.value === '') return '—'
  if (typeof props.value === 'string') return props.value
  if (props.format === 'percent') return `${props.value.toFixed(1)}%`
  const options = props.format === 'currency' && props.currency
    ? { style: 'currency', currency: props.currency, maximumFractionDigits: 0 }
    : { maximumFractionDigits: 2 }
  return new Intl.NumberFormat(undefined, options).format(props.value)
})

const valueClass = computed(() => {
  if (props.theme === 'red') return 'text-polaris-critical'
  if (props.theme === 'green') return 'text-polaris-success'
  return 'text-polaris-text'
})
</script>
