<template>
  <!-- Embedded Settings > Store Connection: what the Shopify install set up, read-only.
       Credentials, store URL and API version come from the install, so they are not shown. -->
  <div class="flex flex-col gap-4">
    <div v-if="status.loading && !status.data" class="flex justify-center p-6 text-ink-gray-5">
      <LoadingIndicator class="size-5" />
    </div>
    <template v-else-if="d">
      <SectionCard title="Connected shop">
        <dl class="grid grid-cols-1 gap-3 text-base md:grid-cols-2">
          <div>
            <dt class="text-sm text-ink-gray-5">Shop</dt>
            <dd class="text-ink-gray-9">{{ d.shop_name || d.shop }} <span v-if="d.shop_name" class="text-ink-gray-5">({{ d.shop }})</span></dd>
          </div>
          <div>
            <dt class="text-sm text-ink-gray-5">Status</dt>
            <dd><StatusPill :status="d.enabled ? 'Connected' : 'Disconnected'" :label="d.enabled ? 'Connected' : d.status || 'Disconnected'" /></dd>
          </div>
          <div>
            <dt class="text-sm text-ink-gray-5">Order mode</dt>
            <dd class="text-ink-gray-9">{{ d.order_mode || '—' }}</dd>
          </div>
          <div>
            <dt class="text-sm text-ink-gray-5">Installed on</dt>
            <dd class="text-ink-gray-9">{{ d.installed_on ? formatDateTime(d.installed_on) : '—' }}</dd>
          </div>
        </dl>
      </SectionCard>
      <SectionCard title="Granted scopes">
        <div v-if="d.scopes.length" class="flex flex-wrap gap-1.5">
          <Badge v-for="scope in d.scopes" :key="scope" :label="scope" variant="subtle" theme="gray" />
        </div>
        <p v-else class="text-base text-ink-gray-5">No scopes recorded.</p>
      </SectionCard>
      <SectionCard title="Locations" flush>
        <ul v-if="d.locations.length" class="divide-y divide-outline-gray-1">
          <li v-for="loc in d.locations" :key="loc.location_id" class="flex items-center justify-between px-4 py-2.5 text-base">
            <span class="text-ink-gray-9">{{ loc.location_name || loc.location_id }}</span>
            <span class="flex gap-1.5">
              <Badge v-if="loc.location_id === d.default_location_id" label="Default" theme="blue" variant="subtle" />
              <Badge v-if="loc.sell_from" label="Sell from here" theme="green" variant="subtle" />
            </span>
          </li>
        </ul>
        <p v-else class="p-4 text-base text-ink-gray-5">No locations fetched yet.</p>
      </SectionCard>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Badge, LoadingIndicator, createResource } from 'frappe-ui'
import SectionCard from '@/components/SectionCard.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime } from '@/utils/format'

const status = createResource({
  url: 'field_sales.shopify.embedded.get_store_status',
  method: 'GET',
  auto: true,
})
const d = computed(() => status.data)
</script>
