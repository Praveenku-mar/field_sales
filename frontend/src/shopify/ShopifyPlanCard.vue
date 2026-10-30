<template>
  <!-- Shopify App Pricing plan (OAuth installs only; renders nothing otherwise). -->
  <SectionCard v-if="info.data?.oauth" title="App plan">
    <template #actions>
      <Button
        v-if="info.data.pricing_url"
        label="Change plan"
        icon-left="lucide-external-link"
        @click="openPricing"
      />
    </template>
    <p v-if="!info.data.known" class="text-sm text-polaris-secondary">
      The plan can't be read (Partner API not configured), so no plan limits apply.
    </p>
    <dl v-else class="grid grid-cols-1 gap-4 text-sm md:grid-cols-3">
      <div>
        <dt class="text-xs font-medium text-polaris-secondary">Plan</dt>
        <dd class="mt-1 font-semibold text-polaris-text">{{ info.data.plan || 'No active plan' }}</dd>
      </div>
      <div v-for="(label, kind) in KINDS" :key="kind">
        <dt class="text-xs font-medium text-polaris-secondary">{{ label }}</dt>
        <dd class="mt-1 font-semibold text-polaris-text">
          {{ info.data.usage[kind] }}
          <span v-if="info.data.limits[kind] != null" class="font-normal text-polaris-secondary"> of {{ info.data.limits[kind] }}</span>
          <span v-else class="font-normal text-polaris-secondary"> (no limit)</span>
        </dd>
      </div>
    </dl>
  </SectionCard>
</template>

<script setup>
import { Button, createResource } from 'frappe-ui'
import SectionCard from '@/components/SectionCard.vue'

const KINDS = { sales_reps: 'Active sales reps', orders_per_month: 'Orders this month' }

const info = createResource({ url: 'field_sales.shopify.billing.get_plan_info', method: 'GET', auto: true })

// Shopify's plan page must open in the admin's own window, not a pop-up (App Store rule).
function openPricing() {
  window.open(info.data.pricing_url, '_top')
}
</script>
