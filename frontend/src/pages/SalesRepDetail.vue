<template>
  <div>
    <PageHeader :title="d.full_name || name" :status="d.name ? (d.active ? 'Active' : 'Inactive') : null">
      <template #meta><span class="text-ink-gray-5">{{ name }}</span></template>
      <template #actions>
        <Dropdown v-if="d.name" :options="moreActions" align="end">
          <Button variant="ghost" icon="lucide-ellipsis" aria-label="More actions" />
        </Dropdown>
        <Button v-if="canEdit" label="Edit" icon-left="lucide-pencil" variant="solid" @click="openEdit" />
      </template>
    </PageHeader>

    <div v-if="doc.get.error" class="px-5"><div class="fs-card"><EmptyState icon="lucide-circle-alert" title="Sales rep not found" :description="errorText" /></div></div>

    <div v-else class="grid grid-cols-1 items-start gap-4 px-5 pb-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div class="space-y-4">
        <SectionCard title="Details">
          <InfoGrid :items="details" :columns="3" />
        </SectionCard>

        <SectionCard v-if="team.data?.length" :title="`Team (${team.data.length})`" flush>
          <DataTable :columns="teamColumns" :rows="team.data" :row-route="(r) => ({ name: 'SalesRep', params: { name: r.name } })">
            <template #cell-full_name="{ row }">
              <div class="font-medium text-ink-gray-9">{{ row.full_name }}</div>
              <div class="text-xs text-ink-gray-5">{{ row.name }}</div>
            </template>
            <template #cell-achievement="{ row }"><Achievement :pct="teamStats.data?.[row.name]?.achievement_pct ?? null" /></template>
            <template #cell-active="{ row }"><StatusPill :status="row.active ? 'Active' : 'Inactive'" /></template>
          </DataTable>
        </SectionCard>

        <SectionCard title="Recent Visits" flush>
          <DataTable :columns="visitColumns" :rows="visits.data || []" :loading="visits.list.loading" :row-route="(r) => ({ name: 'Visit', params: { name: r.name } })" empty-icon="lucide-map-pin" empty-title="No visits yet">
            <template #cell-status="{ value }"><StatusPill :status="value" /></template>
          </DataTable>
        </SectionCard>

        <SectionCard title="Recent Orders" flush>
          <DataTable :columns="orderColumns" :rows="orders.data || []" :loading="orders.list.loading" :row-route="(r) => ({ name: 'Order', params: { name: r.name } })" empty-icon="lucide-shopping-cart" empty-title="No orders yet">
            <template #cell-sync_status="{ row }"><StatusPill :status="orderStatus(row)" /></template>
          </DataTable>
        </SectionCard>
      </div>

      <div class="space-y-4">
        <div class="fs-card">
          <div class="flex items-center gap-2 border-b border-desk-border px-4 py-3">
            <span class="lucide-chart-column size-4 text-ink-gray-7" />
            <h2 class="flex-1 text-base font-semibold text-ink-gray-9">This Month</h2>
            <span class="text-ink-gray-5">{{ monthLabel }}</span>
          </div>
          <div v-if="month.loading && !month.data" class="flex justify-center py-8"><LoadingIndicator class="size-4" /></div>
          <div v-else-if="!m" class="px-4 py-6 text-center text-ink-gray-5">No activity this month</div>
          <template v-else>
            <div class="space-y-2 px-4 py-3">
              <div class="flex items-baseline justify-between">
                <span class="text-2xl font-semibold tabular-nums text-ink-gray-9">{{ formatCurrency(m.order_value, undefined, 0) }}</span>
                <span v-if="m.period_target" class="text-ink-gray-5">of {{ formatCurrency(m.period_target, undefined, 0) }}</span>
              </div>
              <Achievement :pct="m.achievement_pct" />
              <p v-if="m.period_target" class="text-xs text-ink-gray-5">
                Target to date, pro-rated from {{ formatCurrency(m.monthly_target, undefined, 0) }} a month
              </p>
            </div>
            <dl class="divide-y divide-outline-gray-1 border-t border-desk-border px-4">
              <div v-for="item in monthRows" :key="item.label" class="flex items-center justify-between py-2.5">
                <dt class="text-ink-gray-6">{{ item.label }}</dt>
                <dd class="tabular-nums" :class="item.alert ? 'font-medium text-ink-red-6' : 'text-ink-gray-8'">{{ item.value }}</dd>
              </div>
            </dl>
          </template>
          <div class="p-4 pt-1">
            <Button v-if="session.isManager" class="w-full" label="Open Rep Performance" :href="reportUrl" />
          </div>
        </div>

        <SectionCard title="Retailers">
          <div class="flex items-baseline justify-between">
            <span class="text-2xl font-semibold tabular-nums text-ink-gray-9">{{ retailerCount.data ?? '—' }}</span>
            <span class="text-ink-gray-5">active retailers assigned</span>
          </div>
        </SectionCard>
      </div>
    </div>

    <Dialog v-model="editOpen" title="Edit sales rep" size="lg">
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormControl v-model="edit.mobile" label="Mobile" type="tel" class="sm:col-span-2" />
        <FormControl v-model="edit.territory" label="Territory" />
        <FormControl v-model.number="edit.monthly_target" label="Monthly Target" type="number" min="0" />
        <div class="space-y-1.5 sm:col-span-2">
          <div class="text-sm text-ink-gray-6">Reports To</div>
          <LinkField
            v-model="edit.reports_to"
            class="w-full"
            doctype="Sales Rep"
            title-field="full_name"
            description-field="territory"
            :search-fields="['full_name', 'name', 'territory']"
            :filters="{ active: 1, name: ['!=', name] }"
            :label="manager.data?.full_name || d.reports_to"
            placeholder="No manager"
          />
        </div>
        <Checkbox v-model="edit.active" label="Active" class="sm:col-span-2" />
      </div>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" @click="editOpen = false" />
          <Button variant="solid" label="Save" :loading="doc.setValue.loading" @click="saveEdit" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import {
  Button,
  Checkbox,
  Dialog,
  Dropdown,
  FormControl,
  LoadingIndicator,
  createDocumentResource,
  createListResource,
  createResource,
  toast,
} from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import InfoGrid from '@/components/InfoGrid.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import Achievement from '@/components/Achievement.vue'
import LinkField from '@/components/LinkField.vue'
import { useSessionStore } from '@/stores/session'
import { formatCurrency, formatDate, formatNumber, orderStatus } from '@/utils/format'

const props = defineProps({ name: { type: String, required: true } })
const session = useSessionStore()

const doc = createDocumentResource({ doctype: 'Sales Rep', name: props.name, auto: true })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))
// Sales Rep is writable by managers only (doctype permissions).
const canEdit = computed(() => !!d.value.name && session.isManager)

// The manager's name; the document only stores their ID.
const manager = createResource({
  url: 'frappe.client.get_value',
  makeParams: () => ({ doctype: 'Sales Rep', filters: d.value.reports_to, fieldname: 'full_name' }),
})
watch(
  () => d.value.reports_to,
  (value) => value && manager.fetch(),
  { immediate: true },
)

const details = computed(() => [
  { label: 'Email', value: d.value.user, href: d.value.user && `mailto:${d.value.user}` },
  { label: 'Mobile', value: d.value.mobile, href: d.value.mobile && `tel:${d.value.mobile}` },
  { label: 'Territory', value: d.value.territory },
  {
    label: 'Reports To',
    value: d.value.reports_to ? manager.data?.full_name || d.value.reports_to : null,
    route: d.value.reports_to && { name: 'SalesRep', params: { name: d.value.reports_to } },
  },
  { label: 'Monthly Target', value: d.value.monthly_target ? formatCurrency(d.value.monthly_target, undefined, 0) : null },
])

// ---------------------------------------------------------------- related lists

const team = createListResource({
  doctype: 'Sales Rep',
  fields: ['name', 'full_name', 'territory', 'active'],
  filters: { reports_to: props.name },
  orderBy: 'full_name asc',
  pageLength: 50,
  auto: true,
})
const teamStats = createResource({ url: 'field_sales.frontend_api.get_rep_month_stats' })
watch(
  () => team.data?.length,
  (n) => n && teamStats.fetch(),
)
const teamColumns = [
  { key: 'full_name', label: 'Name' },
  { key: 'territory', label: 'Territory' },
  { key: 'achievement', label: 'This Month', width: '180px' },
  { key: 'active', label: 'Status' },
]

const visits = createListResource({
  doctype: 'Visit',
  fields: ['name', 'retailer_name', 'city', 'visit_date', 'status', 'time_on_site_min'],
  filters: { sales_rep: props.name },
  orderBy: 'visit_date desc, modified desc',
  pageLength: 5,
  auto: true,
})
const visitColumns = [
  { key: 'visit_date', label: 'Date', format: formatDate },
  { key: 'retailer_name', label: 'Retailer' },
  { key: 'city', label: 'City' },
  { key: 'time_on_site_min', label: 'On Site', align: 'right', format: (v, r) => (r.status === 'Completed' ? `${v || 0} min` : '—') },
  { key: 'status', label: 'Status' },
]

const orders = createListResource({
  doctype: 'Field Order',
  fields: ['name', 'retailer_name', 'order_date', 'grand_total', 'docstatus', 'sync_status'],
  filters: { sales_rep: props.name, docstatus: ['!=', 2] },
  orderBy: 'order_date desc, modified desc',
  pageLength: 5,
  auto: true,
})
const orderColumns = [
  { key: 'name', label: 'Order' },
  { key: 'retailer_name', label: 'Retailer' },
  { key: 'order_date', label: 'Date', format: formatDate },
  { key: 'grand_total', label: 'Total', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'sync_status', label: 'Status' },
]

const retailerCount = createResource({
  url: 'frappe.desk.reportview.get_count',
  params: { doctype: 'Retailer', filters: { sales_rep: props.name, status: 'Active' } },
  auto: true,
})

// ---------------------------------------------------------------- this month

const month = createResource({
  url: 'field_sales.frontend_api.get_rep_month_stats',
  params: { sales_rep: props.name },
  auto: true,
})
const m = computed(() => month.data?.[props.name] || null)
const monthLabel = new Date().toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
const pct = (v) => (v == null ? '—' : `${formatNumber(v, 1)}%`)
const monthRows = computed(() => [
  { label: 'Orders', value: `${formatNumber(m.value.orders)}${m.value.avg_order_value ? ` · avg ${formatCurrency(m.value.avg_order_value, undefined, 0)}` : ''}` },
  { label: 'Visits completed', value: `${formatNumber(m.value.completed_visits)} of ${formatNumber(m.value.planned_visits)}` },
  { label: 'Missed visits', value: formatNumber(m.value.missed_visits), alert: m.value.missed_visits > 0 },
  { label: 'Visit → order', value: pct(m.value.conversion_pct) },
  { label: 'Retailers visited', value: formatNumber(m.value.retailers_visited) },
  { label: 'Failed syncs', value: formatNumber(m.value.failed_syncs), alert: m.value.failed_syncs > 0 },
])

const reportUrl = computed(() => `/app/query-report/Rep Performance?sales_rep=${encodeURIComponent(props.name)}`)

const moreActions = computed(() => [
  { label: 'Open in Desk', icon: 'lucide-app-window', onClick: () => (window.location.href = `/app/sales-rep/${encodeURIComponent(props.name)}`) },
  { label: 'Open User', icon: 'lucide-user-cog', onClick: () => (window.location.href = `/app/user/${encodeURIComponent(props.name)}`), condition: () => session.isAdmin },
].filter((a) => !a.condition || a.condition()))

// ---------------------------------------------------------------- edit

const editOpen = ref(false)
const edit = reactive({})
// full_name is fetched from the User, so it is not editable here.
const EDIT_FIELDS = ['mobile', 'territory', 'monthly_target', 'reports_to', 'active']

function openEdit() {
  EDIT_FIELDS.forEach((f) => (edit[f] = d.value[f] ?? null))
  edit.active = !!d.value.active
  editOpen.value = true
}

async function saveEdit() {
  try {
    await doc.setValue.submit({ ...edit, active: edit.active ? 1 : 0, monthly_target: edit.monthly_target || 0 })
    editOpen.value = false
    toast.success('Sales rep updated')
    month.reload()
  } catch (e) {
    toast.error(e?.messages?.[0] || e?.message || 'Could not save')
  }
}

</script>
