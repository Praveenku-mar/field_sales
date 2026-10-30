<template>
  <span
    class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium border"
    :class="tone.container"
  >
    <span v-if="icon" :class="[icon, 'size-3 shrink-0']" />
    <span v-else class="size-1.5 rounded-full shrink-0" :class="tone.dot" />
    <span class="truncate">{{ label || status }}</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, required: true },
  label: { type: String, default: null },
  /** Icon class (e.g. lucide-circle-check) rendered before the label. */
  icon: { type: String, default: null },
})

const TONES = {
  success: {
    container: 'bg-[#e4f6ed] text-[#0d5936] border-[#aee8cb]',
    dot: 'bg-[#108043]',
  },
  attention: {
    container: 'bg-[#fef3d6] text-[#744210] border-[#fbd88e]',
    dot: 'bg-[#b98900]',
  },
  critical: {
    container: 'bg-[#fde8e8] text-[#9b1c1c] border-[#f8b4b4]',
    dot: 'bg-[#d72c0d]',
  },
  info: {
    container: 'bg-[#e0f2fe] text-[#0369a1] border-[#bae6fd]',
    dot: 'bg-[#2c6ecb]',
  },
  subdued: {
    container: 'bg-[#f1f2f4] text-[#4a4a4a] border-[#e1e3e5]',
    dot: 'bg-[#8c9196]',
  },
}

const STATUS_TO_TONE = {
  Synced: 'success',
  Success: 'success',
  Completed: 'success',
  Active: 'success',
  Earned: 'success',
  Paid: 'success',

  Pending: 'attention',
  Retrying: 'attention',
  'In Progress': 'attention',
  'Partially Paid': 'attention',
  'Link Expired': 'attention',

  Failed: 'critical',
  'Out of Stock': 'critical',
  Cancelled: 'critical',
  Reversed: 'critical',
  'Link Cancelled': 'critical',

  Planned: 'info',
  Approved: 'info',
  'Link Sent': 'info',

  Draft: 'subdued',
  Unpaid: 'subdued',
  Inactive: 'subdued',
}

const tone = computed(() => TONES[STATUS_TO_TONE[props.status] || 'subdued'])
</script>
