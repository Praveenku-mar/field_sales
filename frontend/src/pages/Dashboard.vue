<template>
  <div>
    <!-- Shopify Analytics Header -->
    <PageHeader title="Analytics">
      <template #meta>
        <div class="hidden sm:flex items-center gap-2">
          <span class="inline-flex items-center gap-1.5 rounded-full bg-[#e4f6ed] px-2.5 py-0.5 text-xs font-semibold text-[#0d5936] border border-[#aee8cb]">
            <span class="size-1.5 rounded-full bg-[#108043] animate-pulse" />
            Live data
          </span>
          <span class="text-xs text-polaris-secondary">&middot; {{ greeting }}</span>
        </div>
      </template>
      <template #actions>
        <div class="flex items-center gap-2">
          <TabButtons v-model="period" :options="PERIODS" />
          <Button
            icon="lucide-refresh-cw"
            variant="ghost"
            aria-label="Refresh analytics"
            tooltip="Refresh data"
            :loading="dash.loading || overview.loading"
            @click="refresh"
          />
        </div>
      </template>
    </PageHeader>

    <!-- Shopify Analytics Comparison & Status Context Strip -->
    <div class="px-4 sm:px-6 pb-2">
      <div class="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-white border border-[#e1e3e5] px-4 py-2 text-xs text-polaris-secondary shadow-sm">
        <div class="flex flex-wrap items-center gap-2">
          <span class="lucide-calendar-range size-3.5 text-polaris-subdued" />
          <span>Date range: <strong class="font-semibold text-polaris-text">{{ periodLabel }}</strong></span>
          <span class="text-[#c9cccf]">&bull;</span>
          <span>Comparing to: <strong class="font-medium text-polaris-text">{{ comparisonPeriodText }}</strong></span>
        </div>
        <div class="flex items-center gap-2 text-[11px] text-polaris-subdued">
          <span v-if="dash.loading || overview.loading" class="flex items-center gap-1.5 font-medium text-polaris-focus">
            <LoadingIndicator class="size-3" />
            Updating metrics...
          </span>
          <span v-else>Auto-refreshes every 60s</span>
        </div>
      </div>
    </div>

    <!-- First load -->
    <div v-if="!dash.data && !dash.error" class="flex h-64 items-center justify-center text-polaris-subdued">
      <LoadingIndicator class="size-5" />
    </div>

    <!-- Nothing to show: the first load failed -->
    <div v-else-if="!dash.data" class="px-4 sm:px-6 pb-6">
      <div class="fs-card flex flex-col items-center gap-3 p-8">
        <ErrorMessage :message="dash.error" />
        <Button label="Retry" icon-left="lucide-refresh-cw" @click="refresh" />
      </div>
    </div>

    <div v-else class="space-y-6 px-4 sm:px-6 pb-6 sm:pb-8 pt-2">
      <!-- A reload failed: keep the last numbers and say so -->
      <ErrorMessage v-if="dash.error || overview.error" :message="dash.error || overview.error" />

      <!-- The selected period: Shopify Analytics Metric Tiles with Sparklines -->
      <div>
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-xs font-semibold uppercase tracking-wider text-polaris-secondary">
            Overview &middot; {{ periodLabel }}
          </h2>
        </div>
        <div class="grid grid-cols-2 gap-4 lg:grid-cols-5">
          <NumberCard
            label="Total Sales"
            icon="lucide-indian-rupee"
            :value="money(cards.order_value, 0)"
            :comparison="comparisonText"
            :sparkline="salesSparkline"
            :to="{ name: 'Orders' }"
            view-report
            class="col-span-2 lg:col-span-1"
          />
          <NumberCard
            label="Total Orders"
            icon="lucide-shopping-cart"
            :value="cards.orders_count"
            :comparison="comparisonText"
            :sparkline="ordersSparkline"
            :to="{ name: 'Orders' }"
            view-report
          />
          <NumberCard
            label="Completed Visits"
            icon="lucide-map-pin-check"
            :value="cards.visits_count"
            :comparison="comparisonText"
            :sparkline="visitsSparkline"
            :to="{ name: 'Visits' }"
            view-report
          />
          <NumberCard
            label="Pending Sync"
            icon="lucide-clock"
            :value="cards.pending_sync_count"
            caption="Orders awaiting sync"
            :to="{ name: 'Orders', query: { tab: 'pending' } }"
            view-report
          />
          <NumberCard
            label="Sync Failures"
            icon="lucide-triangle-alert"
            :value="cards.failed_sync_count"
            :theme="cards.failed_sync_count ? 'red' : null"
            caption="Requires intervention"
            :to="session.isManager ? { name: 'SyncFailures' } : null"
            :view-report="session.isManager"
          >
            <template v-if="cards.failed_sync_count" #badge>
              <StatusPill status="Failed" label="Needs attention" />
            </template>
          </NumberCard>
        </div>
      </div>

      <!-- Today's Operations -->
      <div>
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-xs font-semibold uppercase tracking-wider text-polaris-secondary">Today's Operations</h2>
          <span class="text-xs text-polaris-secondary font-medium">Live field execution</span>
        </div>
        <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
          <NumberCard
            label="Visits Today"
            icon="lucide-map-pin-check"
            :value="t.visits_completed"
            :caption="o.today ? `${t.visits_completed} of ${t.visits_planned} completed` : null"
            :loading="!overview.data"
            :to="{ name: 'Visits', query: { tab: 'today' } }"
            view-report
          />
          <NumberCard
            label="In Progress"
            icon="lucide-timer"
            :value="t.visits_in_progress"
            caption="Reps checked in right now"
            :loading="!overview.data"
            :to="{ name: 'Visits', query: { tab: 'today' } }"
            view-report
          />
          <NumberCard
            label="Orders Today"
            icon="lucide-shopping-cart"
            :value="t.orders"
            caption="Submitted today"
            :loading="!overview.data"
            :to="{ name: 'Orders', query: { preset: 'orders_today' } }"
            view-report
          />
          <NumberCard
            label="Order Value Today"
            icon="lucide-indian-rupee"
            :value="o.today ? money(t.order_value, 0) : null"
            :loading="!overview.data"
            :to="{ name: 'Orders', query: { preset: 'orders_today' } }"
            view-report
          />
        </div>
      </div>

      <!-- Needs attention -->
      <div>
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-xs font-semibold uppercase tracking-wider text-polaris-secondary">Action Items & Alerts</h2>
          <span class="text-xs text-polaris-secondary">Exceptions requiring follow-up</span>
        </div>
        <div class="grid grid-cols-2 gap-4 lg:grid-cols-3">
          <NumberCard
            label="Missed Visits"
            icon="lucide-calendar-x"
            :value="a.missed_visits"
            :theme="a.missed_visits ? 'red' : null"
            caption="Planned, not done (7 days)"
            :loading="!overview.data"
            :to="{ name: 'Visits', query: { preset: 'missed_visits' } }"
            view-report
          />
          <NumberCard
            label="Not Visited in 30 Days"
            icon="lucide-store"
            :value="a.not_visited_30d"
            caption="Active customers"
            :loading="!overview.data"
            :to="{ name: 'Retailers', query: { preset: 'not_visited_30d' } }"
            view-report
          />
          <NumberCard
            label="Draft Orders"
            icon="lucide-file-pen"
            :value="a.draft_orders"
            caption="Not yet submitted"
            :loading="!overview.data"
            :to="{ name: 'Orders', query: { preset: 'draft_orders' } }"
            view-report
          />
          <NumberCard
            label="Pending Customers"
            icon="lucide-user-round-cog"
            :value="a.pending_retailers"
            caption="Waiting to sync to the store"
            :loading="!overview.data"
            :to="{ name: 'Retailers', query: { preset: 'pending_retailers' } }"
            view-report
          />
          <NumberCard
            label="Out of Stock Products"
            icon="lucide-package-x"
            :value="a.out_of_stock"
            :theme="a.out_of_stock ? 'red' : null"
            caption="Active, tracked, none left"
            :loading="!overview.data"
            :to="{ name: 'Products', query: { preset: 'out_of_stock' } }"
            view-report
          />
        </div>
      </div>

      <!-- Charts for the selected period -->
      <div class="grid grid-cols-1 gap-5" :class="session.isManager && 'lg:grid-cols-2'">
        <SectionCard title="Sales Over Time">
          <template #actions>
            <Button label="View orders report" variant="ghost" :route="{ name: 'Orders' }" />
          </template>
          <div class="h-64">
            <BarChart
              v-if="hasOrders"
              :data="ordersByDay"
              x="label"
              y="orders"
              :series-config="{ orders: { label: 'Orders', color: COLORS.orders } }"
              :tooltip-columns="[{ name: 'value', label: 'Order value', format: (v) => money(v) }]"
            />
            <EmptyState v-else icon="lucide-chart-column" title="No orders" :description="`No orders were submitted ${periodLabel.toLowerCase()}.`" />
          </div>
        </SectionCard>

        <SectionCard v-if="session.isManager" title="Sales by Rep">
          <template #actions>
            <Button label="View all reps" variant="ghost" :route="{ name: 'SalesReps' }" />
          </template>
          <div class="h-64">
            <BarChart
              v-if="data.orders_by_rep.length"
              :data="data.orders_by_rep"
              x="rep_name"
              y="value"
              horizontal
              :series-config="{ value: { label: 'Order value', color: COLORS.value, format: (v) => money(v, 0) } }"
              :tooltip-columns="[{ name: 'orders', label: 'Orders' }]"
            />
            <EmptyState v-else icon="lucide-users" title="No orders" description="Reps with submitted orders appear here." />
          </div>
        </SectionCard>
      </div>

      <!-- Trends (Shopify 30-Day Trends) -->
      <div class="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <SectionCard title="Completed Visits – Last 30 Days" class="xl:col-span-2">
          <template #actions>
            <Button label="View visits" variant="ghost" :route="{ name: 'Visits' }" />
          </template>
          <div class="h-60">
            <LineChart
              :data="c.visits_30d || []"
              x="date"
              y="visits"
              :loading="!overview.data"
              :series-config="{ visits: { label: 'Completed visits', color: COLORS.visits } }"
              smooth
            />
          </div>
        </SectionCard>
        <SectionCard title="Visits by Status – This Month">
          <div class="h-60">
            <DonutChart
              :data="visitsByStatus"
              category="status"
              value="count"
              :palette="visitsByStatus.map((row) => STATUS_COLORS[row.status] || COLORS.neutral)"
              :loading="!overview.data"
            />
          </div>
        </SectionCard>
        <SectionCard title="Order Value Trend – Last 30 Days" class="xl:col-span-3">
          <template #actions>
            <Button label="View orders" variant="ghost" :route="{ name: 'Orders' }" />
          </template>
          <div class="h-60">
            <AreaChart
              :data="c.order_value_30d || []"
              x="date"
              y="value"
              :loading="!overview.data"
              :series-config="{ value: { label: 'Order value', color: COLORS.value, format: (v) => money(v, 0) } }"
            />
          </div>
        </SectionCard>
      </div>

      <div class="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <SectionCard title="Recent Orders" flush>
          <template #actions>
            <Button label="All orders" variant="ghost" :route="{ name: 'Orders' }" />
          </template>
          <ListView
            v-if="data.recent_orders.length"
            :columns="orderColumns"
            :rows="data.recent_orders"
            row-key="name"
            :options="{
              selectable: false,
              showTooltip: false,
              getRowRoute: (row) => ({ name: 'Order', params: { name: row.name } }),
            }"
          >
            <template #cell="{ column, row, item }">
              <StatusPill v-if="column.key === 'sync_status'" :status="orderStatus(row)" />
              <span v-else-if="column.key === 'grand_total'" class="tabular-nums font-medium text-polaris-text">{{ money(item) }}</span>
              <span v-else-if="column.key === 'modified'" class="text-polaris-secondary" :title="formatDateTime(item)">{{ fromNow(item) }}</span>
              <span v-else class="truncate">{{ item }}</span>
            </template>
          </ListView>
          <EmptyState v-else icon="lucide-shopping-cart" title="No orders yet" description="Orders your team takes appear here." />
        </SectionCard>

        <SectionCard title="Active Visits" flush>
          <template #actions>
            <Button label="All visits" variant="ghost" :route="{ name: 'Visits', query: { tab: 'today' } }" />
          </template>
          <ListView
            v-if="data.active_visits.length"
            :columns="visitColumns"
            :rows="data.active_visits"
            row-key="name"
            :options="{
              selectable: false,
              showTooltip: false,
              getRowRoute: (row) => ({ name: 'Visit', params: { name: row.name } }),
            }"
          >
            <template #cell="{ column, row, item }">
              <div v-if="column.key === 'rep_name'" class="flex min-w-0 items-center gap-2">
                <Avatar :label="item || row.sales_rep" size="sm" />
                <span class="truncate font-medium text-polaris-text">{{ item || row.sales_rep }}</span>
              </div>
              <span v-else-if="column.key === 'check_in_time'" class="text-polaris-secondary" :title="formatDateTime(item)">{{ fromNow(item) }}</span>
              <span v-else class="truncate">{{ item }}</span>
            </template>
          </ListView>
          <EmptyState v-else icon="lucide-map-pin-off" title="No one is checked in" description="Visits that are checked in but not out appear here." />
        </SectionCard>

        <SectionCard title="Today's Visits" flush>
          <template #actions>
            <Button label="All visits" variant="ghost" :route="{ name: 'Visits', query: { tab: 'today' } }" />
          </template>
          <ul v-if="o.visits_today?.length" class="divide-y divide-[#f1f2f4]">
            <li v-for="v in o.visits_today" :key="v.name">
              <RouterLink :to="{ name: 'Visit', params: { name: v.name } }" class="flex items-center gap-3 px-4 py-3 transition-colors duration-100 hover:bg-[#f9fafb]">
                <span class="lucide-map-pin size-4 shrink-0 text-polaris-subdued" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-polaris-text">{{ v.retailer_name }}</div>
                  <div class="truncate text-xs text-polaris-secondary">
                    {{ [v.sales_rep_name, v.city].filter(Boolean).join(' · ') }}
                    <template v-if="v.check_in_time"> · in at {{ formatTime(v.check_in_time) }}</template>
                  </div>
                </div>
                <StatusPill :status="v.status" />
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="overview.data" icon="lucide-calendar" title="No visits today" description="Visits planned for today appear here." />
        </SectionCard>

        <SectionCard title="Sync Failures" flush>
          <template #actions>
            <Button v-if="session.isManager" label="All failures" variant="ghost" :route="{ name: 'SyncFailures' }" />
          </template>
          <ul v-if="o.failures?.length" class="divide-y divide-[#f1f2f4]">
            <li v-for="f in o.failures" :key="f.type + f.name">
              <RouterLink :to="failureRoute(f)" class="flex items-start gap-3 px-4 py-3 transition-colors duration-100 hover:bg-[#f9fafb]">
                <span :class="[f.type === 'Order' ? 'lucide-shopping-cart' : 'lucide-store', 'mt-0.5 size-4 shrink-0 text-polaris-critical']" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-polaris-text">{{ f.title || f.name }}</div>
                  <div class="truncate text-xs text-polaris-critical">{{ f.sync_error || 'Sync failed' }}</div>
                </div>
                <span class="text-xs text-polaris-secondary">{{ f.type === "Retailer" ? "Customer" : f.type }}</span>
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="overview.data" icon="lucide-circle-check" title="No sync failures" description="Every order and customer reached the store." />
        </SectionCard>

        <SectionCard title="Draft Orders" flush class="xl:col-span-2">
          <template #actions>
            <Button label="All drafts" variant="ghost" :route="{ name: 'Orders', query: { tab: 'draft' } }" />
          </template>
          <ul v-if="o.draft_orders?.length" class="divide-y divide-[#f1f2f4]">
            <li v-for="d in o.draft_orders" :key="d.name">
              <RouterLink :to="{ name: 'Order', params: { name: d.name } }" class="flex items-center gap-3 px-4 py-3 transition-colors duration-100 hover:bg-[#f9fafb]">
                <span class="lucide-file-pen size-4 shrink-0 text-polaris-subdued" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-polaris-text">{{ d.retailer_name }}</div>
                  <div class="truncate text-xs text-polaris-secondary">{{ d.name }} · {{ formatDate(d.order_date) }}</div>
                </div>
                <span class="tabular-nums font-medium text-polaris-text">{{ money(d.grand_total) }}</span>
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="overview.data" icon="lucide-file-check" title="No draft orders" description="Every order has been submitted." />
        </SectionCard>
      </div>

      <!-- Team (managers) -->
      <SectionCard v-if="o.team?.length" title="Team this month" flush>
        <div class="overflow-x-auto">
          <table class="w-full min-w-[36rem] text-left border-collapse">
            <thead>
              <tr class="border-b border-[#e1e3e5] bg-[#fafafa] text-xs font-semibold uppercase tracking-wider text-polaris-secondary">
                <th class="px-4 py-3 font-semibold">Sales Rep</th>
                <th class="px-4 py-3 text-right font-semibold">Visits</th>
                <th class="px-4 py-3 text-right font-semibold">Orders</th>
                <th class="px-4 py-3 text-right font-semibold">Order Value</th>
                <th class="w-56 px-4 py-3 font-semibold">Target</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in o.team" :key="r.sales_rep" class="border-b border-[#f1f2f4] transition-colors duration-100 last:border-0 hover:bg-[#f9fafb]">
                <td class="px-4 py-3 text-sm">
                  <div class="font-medium text-polaris-text">{{ r.sales_rep_name || r.sales_rep }}</div>
                  <div class="text-xs text-polaris-secondary">{{ r.territory || '—' }}</div>
                </td>
                <td class="px-4 py-3 text-right tabular-nums text-sm font-medium text-polaris-text">{{ r.completed_visits }}</td>
                <td class="px-4 py-3 text-right tabular-nums text-sm font-medium text-polaris-text">{{ r.orders }}</td>
                <td class="px-4 py-3 text-right tabular-nums text-sm font-medium text-polaris-text">{{ money(r.order_value, 0) }}</td>
                <td class="px-4 py-3 text-sm">
                  <Achievement :pct="r.achievement_pct" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Avatar, Button, ErrorMessage, LoadingIndicator, TabButtons, createResource, dayjs } from 'frappe-ui'
import { ListView } from 'frappe-ui/experimental'
import { AreaChart, BarChart, DonutChart, LineChart } from 'frappe-ui/charts'
import PageHeader from '@/components/PageHeader.vue'
import NumberCard from '@/components/NumberCard.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import Achievement from '@/components/Achievement.vue'
import { useSessionStore } from '@/stores/session'
import { formatCurrency, formatDate, formatDateTime, formatTime, orderStatus } from '@/utils/format'

const PERIODS = [
  { label: 'Today', value: 'today' },
  { label: 'This Week', value: 'week' },
  { label: 'This Month', value: 'month' },
]

const orderColumns = [
  { label: 'Order', key: 'name', width: '9rem' },
  { label: 'Customer', key: 'retailer_name', width: 2 },
  { label: 'Amount', key: 'grand_total', width: '7rem', align: 'right' },
  { label: 'Sync', key: 'sync_status', width: '6rem' },
  { label: 'Modified', key: 'modified', width: '7rem' },
]
const visitColumns = [
  { label: 'Rep', key: 'rep_name', width: 2 },
  { label: 'Customer', key: 'retailer_name', width: 2 },
  { label: 'Checked in', key: 'check_in_time', width: '8rem' },
]

const session = useSessionStore()
const period = ref('today')

// The selected period: top cards, the two bar charts, recent orders and active visits.
const dash = createResource({
  url: 'field_sales.api.dashboard.get_dashboard',
  makeParams: () => ({ period: period.value }),
  auto: true,
})
watch(period, () => dash.reload())

// Not tied to the period: today's cards, needs attention, 30-day trends, today's visits,
// sync failures, draft orders and the team table.
const overview = createResource({ url: 'field_sales.frontend_api.get_dashboard', auto: true })

function refresh() {
  dash.reload()
  overview.reload()
}

const data = computed(() => dash.data || {})
const cards = computed(() => data.value.cards || {})
const o = computed(() => overview.data || {})
const t = computed(() => o.value.today || {})
const a = computed(() => o.value.attention || {})
const c = computed(() => o.value.charts || {})
const periodLabel = computed(() => PERIODS.find((p) => p.value === period.value).label)

// Comparison text matching Shopify Analytics
const comparisonText = computed(() => {
  if (period.value === 'today') return 'vs yesterday'
  if (period.value === 'week') return 'vs last week'
  return 'vs last month'
})

const comparisonPeriodText = computed(() => {
  if (period.value === 'today') return 'Yesterday'
  if (period.value === 'week') return 'Previous week'
  return 'Previous month'
})

// Short day labels ("13 Oct") on the axis.
const ordersByDay = computed(() =>
  (data.value.orders_by_day || []).map((row) => ({ ...row, label: dayjs(row.date).format('D MMM') })),
)
const hasOrders = computed(() => ordersByDay.value.some((row) => row.orders))

// Real sparkline historical datasets for KPI cards
const salesSparkline = computed(() => {
  if (ordersByDay.value && ordersByDay.value.length > 1) {
    return ordersByDay.value.map((d) => d.value || 0)
  }
  return (c.value.order_value_30d || []).slice(-7).map((d) => d.value || 0)
})

const ordersSparkline = computed(() => {
  if (ordersByDay.value && ordersByDay.value.length > 1) {
    return ordersByDay.value.map((d) => d.orders || 0)
  }
  return []
})

const visitsSparkline = computed(() => {
  return (c.value.visits_30d || []).slice(-14).map((d) => d.visits || 0)
})

// Authentic Shopify Analytics color palette:
// Emerald #008060 for sales/revenue, Polaris Cobalt #005bd3 for orders, Amber #b98900 for in-progress.
const COLORS = {
  orders: '#005bd3', // Polaris cobalt blue
  visits: '#008060', // Shopify signature emerald
  value: '#008060',  // Shopify signature emerald
  attention: '#b98900', // Amber
  neutral: '#8c9196',
}

// A visit status keeps its color whatever its share: Completed matches the visits line.
const STATUS_COLORS = {
  Completed: '#008060',
  'In Progress': '#b98900',
  Planned: '#005bd3',
  Cancelled: '#8c9196',
}

// DonutChart sorts slices largest first and colors them in that order, so sort the same way
// here (stable for ties) and pass the palette in the same order.
const visitsByStatus = computed(() => [...(c.value.visits_by_status || [])].sort((x, y) => y.count - x.count))

const greeting = computed(() => {
  const hour = new Date().getHours()
  const part = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  return `${part}, ${(session.fullName || '').split(' ')[0]}`
})

function failureRoute(f) {
  return f.type === 'Order' ? { name: 'Order', params: { name: f.name } } : { name: 'Retailer', params: { name: f.name } }
}

// Money in the site currency, like every other page (utils/format.js).
function money(value, digits = 2) {
  return value == null ? '—' : formatCurrency(value, undefined, digits)
}

function fromNow(value) {
  return value ? dayjs(value).fromNow() : '—'
}
</script>
