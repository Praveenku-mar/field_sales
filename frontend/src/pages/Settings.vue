<template>
  <div>
    <PageHeader title="Settings" />
    <div class="space-y-3 px-5 pb-5">
      <TabButtons v-model="tab" :options="tabs" />
      <div class="fs-card">
        <EmptyState icon="lucide-settings" :title="current.label" :description="current.description">
          <Button label="Open in Desk" icon-left="lucide-external-link" :href="current.href" />
        </EmptyState>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, TabButtons } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'

const tabs = [
  { label: 'Store Connection', value: 'store', description: 'Connect the store and choose the default location.', href: '/app/store-connection' },
  { label: 'Sales Reps', value: 'reps', description: 'Reps, territories, reporting lines and monthly targets.', href: '/app/sales-rep' },
]
const tab = ref('store')
const current = computed(() => tabs.find((t) => t.value === tab.value))
</script>
