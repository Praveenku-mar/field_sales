<template>
  <div>
    <PageHeader title="Visits">
      <template #actions>
        <Button label="New Visit" icon-left="lucide-plus" variant="solid" href="/app/visit/new" />
      </template>
    </PageHeader>
    <div class="space-y-3 px-5 pb-5">
      <ListFilters v-model="model" search-placeholder="Search retailer" :tabs="tabs" :fields="fields">
        <PresetChip :label="presetLabel" />
      </ListFilters>
      <DataTable
        :columns="columns"
        :rows="list.data || []"
        :loading="list.list.loading"
        :total="count.data"
        :has-more="list.hasNextPage"
        :row-route="(row) => ({ name: 'Visit', params: { name: row.name } })"
        empty-icon="lucide-map-pin-check"
        empty-title="No visits found"
        @load-more="list.next()"
      >
        <template #cell-retailer_name="{ row }">
          <div class="font-medium text-ink-gray-9">{{ row.retailer_name }}</div>
          <div class="text-xs text-ink-gray-5">{{ [row.name, row.city].filter(Boolean).join(' · ') }}</div>
        </template>
        <template #cell-status="{ value }"><StatusPill :status="value" /></template>
        <template #cell-within_geofence="{ row }">
          <span v-if="!row.check_in_time" class="text-ink-gray-4">—</span>
          <span v-else :class="row.within_geofence ? 'text-ink-green-7' : 'text-ink-amber-7'">
            {{ row.within_geofence ? 'Inside' : `${Math.round(row.distance_from_retailer_m || 0)} m away` }}
          </span>
        </template>
      </DataTable>
    </div>
  </div>
</template>

<script setup>
import { Button } from 'frappe-ui'
import { useRoute } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import PresetChip from '@/components/PresetChip.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import { useDocList } from '@/composables/useDocList'
import { formatDate, formatTime, today } from '@/utils/format'

const tabs = [
  { label: 'Today', value: 'today' },
  { label: 'Upcoming', value: 'upcoming' },
  { label: 'Past', value: 'past' },
  { label: 'All', value: 'all' },
]
const fields = [{ fieldname: 'status', label: 'Status', options: ['', 'Planned', 'In Progress', 'Completed', 'Cancelled'] }]
const columns = [
  { key: 'retailer_name', label: 'Retailer' },
  { key: 'sales_rep_name', label: 'Sales Rep' },
  { key: 'visit_date', label: 'Date', format: formatDate },
  { key: 'check_in_time', label: 'Check-in', format: (v) => (v ? formatTime(v) : '—') },
  { key: 'time_on_site_min', label: 'On Site', align: 'right', format: (v, row) => (row.status === 'Completed' ? `${v || 0} min` : '—') },
  { key: 'within_geofence', label: 'Geofence' },
  { key: 'status', label: 'Status' },
]

function buildFilters(m) {
  const filters = []
  if (m.tab === 'today') filters.push(['visit_date', '=', today()])
  if (m.tab === 'upcoming') filters.push(['visit_date', '>', today()])
  if (m.tab === 'past') filters.push(['visit_date', '<', today()])
  if (m.status) filters.push(['status', '=', m.status])
  return filters
}

const route = useRoute()
const { model, list, count, presetLabel } = useDocList({
  doctype: 'Visit',
  fields: ['name', 'retailer_name', 'city', 'sales_rep_name', 'visit_date', 'status', 'check_in_time', 'time_on_site_min', 'within_geofence', 'distance_from_retailer_m'],
  orderBy: 'visit_date desc, modified desc',
  buildFilters,
  searchFields: ['retailer_name', 'name', 'city', 'sales_rep_name'],
  // ?preset=… (dashboard cards) shows exactly what the card counted, under the "All" tab;
  // ?tab=… opens a tab directly.
  initial: { tab: route.query.preset ? 'all' : route.query.tab || 'today' },
  preset: route.query.preset || null,
})
</script>
