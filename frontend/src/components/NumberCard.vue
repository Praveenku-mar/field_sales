<template>
  <component
    :is="to ? 'RouterLink' : 'div'"
    :to="to"
    class="fs-card relative flex flex-col justify-between overflow-hidden p-4 sm:p-5"
    :class="to && 'fs-card-hover group cursor-pointer'"
  >
    <!-- Top row: Metric title and actions -->
    <div>
      <div class="flex items-center justify-between gap-2 text-polaris-secondary">
        <span class="truncate text-xs font-semibold uppercase tracking-wider text-polaris-secondary">
          {{ label }}
        </span>
        <div class="flex items-center gap-1.5">
          <slot v-if="!loading" name="badge" />
          <span v-if="icon" :class="[icon, 'size-4 text-polaris-subdued transition-colors group-hover:text-polaris-text']" />
          <span
            v-if="to && viewReport"
            class="text-[11px] font-semibold text-polaris-focus opacity-70 transition-opacity group-hover:opacity-100 group-hover:underline"
          >
            Report &rarr;
          </span>
        </div>
      </div>

      <!-- Main Metric Value & Trend Badge -->
      <div class="mt-2 flex items-baseline gap-2.5">
        <span v-if="loading" class="h-8 w-24 animate-pulse rounded bg-[#ebebeb]" />
        <span v-else class="text-2xl sm:text-3xl font-bold tracking-tight tabular-nums" :class="valueClass">
          {{ formatted }}
        </span>
        <span
          v-if="delta != null && !loading"
          class="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-semibold"
          :class="delta >= 0 ? 'bg-[#e4f6ed] text-[#0d5936]' : 'bg-[#fde8e8] text-[#9b1c1c]'"
        >
          <span>{{ delta >= 0 ? '↑' : '↓' }}</span>
          <span>{{ Math.abs(delta).toFixed(1) }}%</span>
        </span>
      </div>

      <!-- Comparison caption matching Shopify Analytics -->
      <div v-if="comparison || caption" class="mt-1 flex items-center gap-1.5 truncate text-xs text-polaris-secondary">
        <span v-if="comparison">{{ comparison }}</span>
        <span v-else>{{ caption }}</span>
      </div>
    </div>

    <!-- Mini Sparkline (Shopify Analytics Signature) -->
    <div v-if="sparklinePoints.length > 1 && !loading" class="mt-3.5 h-8 w-full">
      <svg class="h-full w-full overflow-visible" viewBox="0 0 100 28" preserveAspectRatio="none">
        <defs>
          <linearGradient :id="gradientId" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" :stop-color="sparklineColor" stop-opacity="0.22" />
            <stop offset="100%" :stop-color="sparklineColor" stop-opacity="0.0" />
          </linearGradient>
        </defs>
        <path :d="sparklineFillPath" :fill="`url(#${gradientId})`" />
        <path
          :d="sparklinePath"
          fill="none"
          :stroke="sparklineColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </div>
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
  /** Comparison text e.g. "vs yesterday", "vs last week", "vs last month" */
  comparison: { type: String, default: null },
  /** Change vs a comparison period, in percent. */
  delta: { type: Number, default: null },
  theme: { type: String, default: null },
  loading: { type: Boolean, default: false },
  to: { type: [String, Object], default: null },
  viewReport: { type: Boolean, default: false },
  /** Array of numbers for mini sparkline e.g. [12, 19, 8, 24, 30] */
  sparkline: { type: Array, default: () => [] },
})

const uid = Math.random().toString(36).substring(2, 9)
const gradientId = computed(() => `spk-${uid}`)

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

const sparklineColor = computed(() => {
  if (props.theme === 'red') return '#d72c0d'
  if (props.theme === 'green') return '#008060'
  return '#008060' // Shopify signature emerald
})

const sparklinePoints = computed(() => {
  if (!props.sparkline || props.sparkline.length < 2) return []
  const data = props.sparkline.map((v) => Number(v) || 0)
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  return data.map((val, i) => {
    const x = (i / (data.length - 1)) * 100
    const y = 24 - ((val - min) / range) * 20 + 2
    return { x: Number(x.toFixed(1)), y: Number(y.toFixed(1)) }
  })
})

const sparklinePath = computed(() => {
  const pts = sparklinePoints.value
  if (!pts.length) return ''
  return pts.reduce((acc, pt, i) => (i === 0 ? `M ${pt.x} ${pt.y}` : `${acc} L ${pt.x} ${pt.y}`), '')
})

const sparklineFillPath = computed(() => {
  const pts = sparklinePoints.value
  if (!pts.length) return ''
  const line = pts.reduce((acc, pt, i) => (i === 0 ? `M ${pt.x} ${pt.y}` : `${acc} L ${pt.x} ${pt.y}`), '')
  return `${line} L 100 28 L 0 28 Z`
})
</script>
