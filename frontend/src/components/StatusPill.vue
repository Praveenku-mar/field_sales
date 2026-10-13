<template>
  <Badge :label="label || status" :theme="theme" variant="subtle" size="md" />
</template>

<script setup>
import { computed } from 'vue'
import { Badge } from 'frappe-ui'

const props = defineProps({
  status: { type: String, required: true },
  label: { type: String, default: null },
})

// Sync and document states used across Field Order, Retailer, Visit, Sales Rep and Sync Log.
const THEMES = {
  Synced: 'green',
  Success: 'green',
  Completed: 'green',
  Active: 'green',
  Pending: 'amber',
  Retrying: 'amber',
  'In Progress': 'amber',
  Planned: 'blue',
  Failed: 'red',
  'Out of Stock': 'red',
  Cancelled: 'red',
  Draft: 'gray',
  Inactive: 'gray',
}
const theme = computed(() => THEMES[props.status] || 'gray')
</script>
