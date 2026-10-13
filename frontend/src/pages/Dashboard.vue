<template>
  <div>
    <PageHeader :title="greeting">
      <template #meta>
        <span class="text-ink-gray-5">{{ todayLabel }}</span>
      </template>
      <template #actions>
        <Button icon="lucide-refresh-cw" variant="ghost" :loading="dash.loading" @click="dash.reload()" />
        <Button label="Plan Visit" icon-left="lucide-plus" variant="solid" href="/app/visit/new" />
      </template>
    </PageHeader>

    <div class="space-y-5 px-5 pb-6">
      <!-- Today -->
      <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <NumberCard
          label="Visits Today"
          icon="lucide-map-pin-check"
          :value="d.today?.visits_completed"
          :caption="d.today ? `${d.today.visits_completed} of ${d.today.visits_planned} completed` : null"
          :loading="!dash.data"
          :to="{ name: 'Visits', query: { tab: 'today' } }"
        />
        <NumberCard
          label="In Progress"
          icon="lucide-timer"
          :value="d.today?.visits_in_progress"
          caption="Reps checked in right now"
          :loading="!dash.data"
          :to="{ name: 'Visits', query: { tab: 'today' } }"
        />
        <NumberCard
          label="Orders Today"
          icon="lucide-shopping-cart"
          :value="d.today?.orders"
          caption="Submitted today"
          :loading="!dash.data"
          :to="{ name: 'Orders' }"
        />
        <NumberCard
          label="Order Value Today"
          icon="lucide-indian-rupee"
          :value="d.today ? formatCurrency(d.today.order_value, d.currency, 0) : null"
          :loading="!dash.data"
          :to="{ name: 'Orders' }"
        />
      </div>

      <!-- Needs attention -->
      <div>
        <h2 class="mb-2 text-sm font-medium text-ink-gray-6">Needs attention</h2>
        <div class="grid grid-cols-2 gap-4 lg:grid-cols-3">
          <NumberCard
            label="Failed Syncs"
            icon="lucide-triangle-alert"
            :value="a.failed_syncs"
            :theme="a.failed_syncs ? 'red' : null"
            caption="Orders and retailers"
            :loading="!dash.data"
            :to="{ name: 'SyncFailures' }"
          />
          <NumberCard
            label="Missed Visits"
            icon="lucide-calendar-x"
            :value="a.missed_visits"
            :theme="a.missed_visits ? 'red' : null"
            caption="Planned, not done (7 days)"
            :loading="!dash.data"
            :to="{ name: 'Visits', query: { preset: 'missed_visits' } }"
          />
          <NumberCard
            label="Not Visited in 30 Days"
            icon="lucide-store"
            :value="a.not_visited_30d"
            caption="Active retailers"
            :loading="!dash.data"
            :to="{ name: 'Retailers', query: { preset: 'not_visited_30d' } }"
          />
          <NumberCard
            label="Draft Orders"
            icon="lucide-file-pen"
            :value="a.draft_orders"
            caption="Not yet submitted"
            :loading="!dash.data"
            :to="{ name: 'Orders', query: { preset: 'draft_orders' } }"
          />
          <NumberCard
            label="Pending Retailers"
            icon="lucide-user-round-cog"
            :value="a.pending_retailers"
            caption="Waiting to sync to the store"
            :loading="!dash.data"
            :to="{ name: 'Retailers', query: { preset: 'pending_retailers' } }"
          />
          <NumberCard
            label="Out of Stock Products"
            icon="lucide-package-x"
            :value="a.out_of_stock"
            :theme="a.out_of_stock ? 'red' : null"
            caption="Active, tracked, none left"
            :loading="!dash.data"
            :to="{ name: 'Products', query: { preset: 'out_of_stock' } }"
          />
        </div>
      </div>

      <div class="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <!-- Today's visits -->
        <SectionCard title="Today's Visits" flush>
          <template #actions>
            <Button label="All visits" variant="ghost" :route="{ name: 'Visits', query: { tab: 'today' } }" />
          </template>
          <ul v-if="d.visits_today?.length" class="divide-y divide-desk-border">
            <li v-for="v in d.visits_today" :key="v.name">
              <RouterLink :to="{ name: 'Visit', params: { name: v.name } }" class="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-gray-1">
                <span class="lucide-map-pin size-4 shrink-0 text-ink-gray-4" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-ink-gray-9">{{ v.retailer_name }}</div>
                  <div class="truncate text-xs text-ink-gray-5">
                    {{ [v.sales_rep_name, v.city].filter(Boolean).join(' · ') }}
                    <template v-if="v.check_in_time"> · in at {{ formatTime(v.check_in_time) }}</template>
                  </div>
                </div>
                <StatusPill :status="v.status" />
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="dash.data" icon="lucide-calendar" title="No visits today" description="Visits planned for today appear here." />
        </SectionCard>


        <!-- Sync failures -->
        <SectionCard title="Sync Failures" flush>
          <template #actions>
            <Button label="All failures" variant="ghost" :route="{ name: 'SyncFailures' }" />
          </template>
          <ul v-if="d.failures?.length" class="divide-y divide-desk-border">
            <li v-for="f in d.failures" :key="f.type + f.name">
              <RouterLink :to="failureRoute(f)" class="flex items-start gap-3 px-4 py-2.5 hover:bg-surface-gray-1">
                <span :class="[f.type === 'Order' ? 'lucide-shopping-cart' : 'lucide-store', 'mt-0.5 size-4 shrink-0 text-ink-red-5']" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-ink-gray-9">{{ f.title || f.name }}</div>
                  <div class="truncate text-xs text-ink-red-5">{{ f.sync_error || 'Sync failed' }}</div>
                </div>
                <span class="text-xs text-ink-gray-5">{{ f.type }}</span>
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="dash.data" icon="lucide-circle-check" title="No sync failures" description="Every order and retailer reached the store." />
        </SectionCard>
      </div>

      <!-- Charts (same four as the desk workspace) -->
      <div class="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <SectionCard title="Visits – Last 30 Days" class="xl:col-span-2">
          <div class="h-60">
            <LineChart :data="c.visits_30d || []" x="date" y="visits" :loading="!dash.data" :series-config="{ visits: { label: 'Completed visits' } }" smooth />
          </div>
        </SectionCard>
        <SectionCard title="Visits by Status – This Month">
          <div class="h-60">
            <DonutChart :data="c.visits_by_status || []" category="status" value="count" :loading="!dash.data" />
          </div>
        </SectionCard>
        <SectionCard title="Order Value – Last 30 Days" class="xl:col-span-2">
          <div class="h-60">
            <AreaChart :data="c.order_value_30d || []" x="date" y="value" :loading="!dash.data" :series-config="{ value: { label: 'Order value' } }" />
          </div>
        </SectionCard>
        <SectionCard title="Orders by Rep – Last 7 Days">
          <div class="h-60">
            <BarChart v-if="c.orders_by_rep_7d?.length" :data="c.orders_by_rep_7d" x="rep" y="value" horizontal :series-config="{ value: { label: 'Order value' } }" />
            <EmptyState v-else-if="dash.data" icon="lucide-chart-bar" title="No orders this week" />
          </div>
        </SectionCard>
      </div>

      <div class="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <!-- Recent orders -->
        <SectionCard title="Recent Orders" flush>
          <template #actions>
            <Button label="All orders" variant="ghost" :route="{ name: 'Orders' }" />
          </template>
          <ul v-if="d.recent_orders?.length" class="divide-y divide-desk-border">
            <li v-for="o in d.recent_orders" :key="o.name">
              <RouterLink :to="{ name: 'Order', params: { name: o.name } }" class="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-gray-1">
                <span class="lucide-shopping-cart size-4 shrink-0 text-ink-gray-4" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-ink-gray-9">{{ o.retailer_name }}</div>
                  <div class="truncate text-xs text-ink-gray-5">{{ o.name }} · {{ formatDate(o.order_date) }}</div>
                </div>
                <span class="tabular-nums text-ink-gray-8">{{ formatCurrency(o.grand_total, d.currency) }}</span>
                <StatusPill :status="orderStatus(o)" />
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="dash.data" icon="lucide-shopping-cart" title="No orders yet" />
        </SectionCard>

        <!-- Draft orders -->
        <SectionCard title="Draft Orders" flush>
          <template #actions>
            <Button label="All drafts" variant="ghost" :route="{ name: 'Orders', query: { tab: 'draft' } }" />
          </template>
          <ul v-if="d.draft_orders?.length" class="divide-y divide-desk-border">
            <li v-for="o in d.draft_orders" :key="o.name">
              <RouterLink :to="{ name: 'Order', params: { name: o.name } }" class="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-gray-1">
                <span class="lucide-file-pen size-4 shrink-0 text-ink-gray-4" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-ink-gray-9">{{ o.retailer_name }}</div>
                  <div class="truncate text-xs text-ink-gray-5">{{ o.name }} · {{ formatDate(o.order_date) }}</div>
                </div>
                <span class="tabular-nums text-ink-gray-8">{{ formatCurrency(o.grand_total, d.currency) }}</span>
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="dash.data" icon="lucide-file-check" title="No draft orders" description="Every order has been submitted." />
        </SectionCard>
      </div>

      <!-- Team (managers) -->
      <SectionCard v-if="d.team?.length" title="Team this month" flush>
        <template #actions>
          <Button label="Rep Performance" variant="ghost" icon-right="lucide-arrow-up-right" href="/app/query-report/Rep Performance" />
        </template>
        <table class="w-full text-left">
          <thead>
            <tr class="border-b border-desk-border text-xs text-ink-gray-5">
              <th class="px-4 py-2 font-medium">Sales Rep</th>
              <th class="px-4 py-2 text-right font-medium">Visits</th>
              <th class="px-4 py-2 text-right font-medium">Orders</th>
              <th class="px-4 py-2 text-right font-medium">Order Value</th>
              <th class="w-56 px-4 py-2 font-medium">Target</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in d.team" :key="r.sales_rep" class="border-b border-desk-border last:border-0">
              <td class="px-4 py-2.5">
                <div class="font-medium text-ink-gray-9">{{ r.sales_rep_name || r.sales_rep }}</div>
                <div class="text-xs text-ink-gray-5">{{ r.territory || '—' }}</div>
              </td>
              <td class="px-4 py-2.5 text-right tabular-nums">{{ r.completed_visits }}</td>
              <td class="px-4 py-2.5 text-right tabular-nums">{{ r.orders }}</td>
              <td class="px-4 py-2.5 text-right tabular-nums">{{ formatCurrency(r.order_value, d.currency, 0) }}</td>
              <td class="px-4 py-2.5">
                <Achievement :pct="r.achievement_pct" />
              </td>
            </tr>
          </tbody>
        </table>
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Button, createResource } from 'frappe-ui'
import { AreaChart, BarChart, DonutChart, LineChart } from 'frappe-ui/charts'
import PageHeader from '@/components/PageHeader.vue'
import NumberCard from '@/components/NumberCard.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import Achievement from '@/components/Achievement.vue'
import { useSessionStore } from '@/stores/session'
import { formatCurrency, formatDate, formatTime, orderStatus } from '@/utils/format'

const session = useSessionStore()

// Fetched fresh on every visit (no cache), so the cards match the lists they open.
const dash = createResource({ url: 'field_sales.frontend_api.get_dashboard', auto: true })
const d = computed(() => dash.data || {})
const a = computed(() => d.value.attention || {})
const c = computed(() => d.value.charts || {})

function failureRoute(f) {
  return f.type === 'Order' ? { name: 'Order', params: { name: f.name } } : { name: 'Retailer', params: { name: f.name } }
}

const greeting = computed(() => {
  const hour = new Date().getHours()
  const part = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  return `${part}, ${(session.fullName || '').split(' ')[0]}`
})
const todayLabel = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
</script>
