<template>
  <div>
    <PageHeader title="Field Orders">
      <template #actions>
        <Button label="New Order" icon-left="lucide-plus" variant="solid" href="/app/field-order/new" />
      </template>
    </PageHeader>
    <div class="space-y-3 px-5 pb-5">
      <ListFilters v-model="model" search-placeholder="Search order or retailer" :tabs="tabs">
        <PresetChip :label="presetLabel" />
      </ListFilters>
      <DataTable
        :columns="columns"
        :rows="list.data || []"
        :loading="list.list.loading"
        :total="count.data"
        :has-more="list.hasNextPage"
        :row-route="(row) => ({ name: 'Order', params: { name: row.name } })"
        empty-icon="lucide-shopping-cart"
        empty-title="No orders found"
        @load-more="list.next()"
      >
        <template #cell-name="{ row }">
          <div class="font-medium text-ink-gray-9">{{ row.retailer_name }}</div>
          <div class="text-xs text-ink-gray-5">{{ row.name }}</div>
        </template>
        <template #cell-sync_status="{ row }">
          <Tooltip :text="row.sync_error" :disabled="!row.sync_error">
            <StatusPill :status="orderStatus(row)" />
          </Tooltip>
        </template>
      </DataTable>
    </div>
  </div>
</template>

<script setup>
import { Button, Tooltip } from 'frappe-ui'
import { useRoute } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import PresetChip from '@/components/PresetChip.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import { useDocList } from '@/composables/useDocList'
import { formatCurrency, formatDate, formatNumber, orderStatus } from '@/utils/format'

const tabs = [
  { label: 'All', value: 'all' },
  { label: 'Drafts', value: 'draft' },
  { label: 'Synced', value: 'synced' },
  { label: 'Pending', value: 'pending' },
  { label: 'Failed', value: 'failed' },
]
const columns = [
  { key: 'name', label: 'Order' },
  { key: 'sales_rep_name', label: 'Sales Rep' },
  { key: 'order_date', label: 'Date', format: formatDate },
  { key: 'total_qty', label: 'Units', align: 'right', format: (v) => formatNumber(v) },
  { key: 'grand_total', label: 'Total', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'sync_status', label: 'Status' },
]

function buildFilters(m) {
  const filters = []
  const byTab = {
    draft: [['docstatus', '=', 0]],
    synced: [['docstatus', '=', 1], ['sync_status', '=', 'Synced']],
    pending: [['docstatus', '=', 1], ['sync_status', '=', 'Pending']],
    failed: [['docstatus', '=', 1], ['sync_status', '=', 'Failed']],
  }
  filters.push(...(byTab[m.tab] || [['docstatus', '!=', 2]]))
  return filters
}

const route = useRoute()
const { model, list, count, presetLabel } = useDocList({
  doctype: 'Field Order',
  fields: ['name', 'retailer_name', 'sales_rep', 'sales_rep.full_name as sales_rep_name', 'order_date', 'total_qty', 'grand_total', 'docstatus', 'sync_status', 'sync_error'],
  orderBy: 'order_date desc, creation desc',
  buildFilters,
  searchFields: ['retailer_name', 'name'],
  // ?preset=… (dashboard cards) shows exactly what the card counted, under the "All" tab;
  // ?tab=… opens a tab directly.
  initial: { tab: route.query.preset ? 'all' : route.query.tab || 'all' },
  preset: route.query.preset || null,
})
</script>
