<template>
  <div>
    <PageHeader title="Products">
      <template #meta>
        <span class="text-ink-gray-5">Synced from the store</span>
      </template>
    </PageHeader>
    <div class="space-y-3 px-5 pb-5">
      <ListFilters v-model="model" search-placeholder="Search title or SKU" :tabs="tabs">
        <PresetChip :label="presetLabel" />
      </ListFilters>
      <DataTable
        :columns="columns"
        :rows="list.data || []"
        :loading="list.list.loading"
        :total="count.data"
        :has-more="list.hasNextPage"
        :row-route="(row) => ({ name: 'Product', params: { name: row.name } })"
        empty-icon="lucide-package"
        empty-title="No products found"
        @load-more="list.next()"
      >
        <template #cell-product_title="{ row }">
          <div class="flex items-center gap-3">
            <img v-if="row.image" :src="row.image" class="size-8 shrink-0 rounded-3 border border-desk-border object-cover" />
            <div v-else class="flex size-8 shrink-0 items-center justify-center rounded-3 bg-surface-gray-2">
              <span class="lucide-package size-4 text-ink-gray-4" />
            </div>
            <div class="min-w-0">
              <div class="truncate font-medium text-ink-gray-9">{{ row.product_title }}</div>
              <div class="truncate text-xs text-ink-gray-5">{{ row.variant_title || row.collection || '—' }}</div>
            </div>
          </div>
        </template>
        <template #cell-available_qty="{ row }">
          <span v-if="!row.track_inventory" class="text-ink-gray-4">Not tracked</span>
          <span v-else :class="stockClass(row.available_qty)">{{ formatNumber(row.available_qty) }}</span>
        </template>
        <template #cell-status="{ value }"><StatusPill :status="value === 'Active' ? 'Active' : value" /></template>
      </DataTable>
    </div>
  </div>
</template>

<script setup>
import { useRoute } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import PresetChip from '@/components/PresetChip.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import { useDocList } from '@/composables/useDocList'
import { formatCurrency, formatNumber } from '@/utils/format'

const LOW_STOCK = 5
const tabs = [
  { label: 'Active', value: 'Active' },
  { label: 'Low / Out of Stock', value: 'low' },
  { label: 'All', value: 'all' },
]
const columns = [
  { key: 'product_title', label: 'Product' },
  { key: 'sku', label: 'SKU', format: (v) => v || '—' },
  { key: 'price', label: 'Price', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'available_qty', label: 'Stock', align: 'right' },
  { key: 'status', label: 'Status' },
]

function buildFilters(m) {
  const filters = []
  if (m.tab === 'Active') filters.push(['status', '=', 'Active'])
  if (m.tab === 'low') {
    filters.push(['status', '=', 'Active'])
    filters.push(['track_inventory', '=', 1])
    filters.push(['available_qty', '<=', LOW_STOCK])
  }
  return filters
}

const route = useRoute()
const { model, list, count, presetLabel } = useDocList({
  doctype: 'Platform Product',
  fields: ['name', 'product_title', 'variant_title', 'collection', 'sku', 'price', 'available_qty', 'track_inventory', 'status', 'image'],
  orderBy: 'product_title asc',
  buildFilters,
  searchFields: ['product_title', 'variant_title', 'sku'],
  // ?preset=… (dashboard cards) shows exactly what the card counted, under the "All" tab;
  // ?tab=… opens a tab directly.
  initial: { tab: route.query.preset ? 'all' : route.query.tab || 'Active' },
  preset: route.query.preset || null,
})

function stockClass(qty) {
  return qty <= 0 ? 'font-medium text-ink-red-5' : qty <= LOW_STOCK ? 'text-ink-amber-7' : 'text-ink-green-7'
}
</script>
