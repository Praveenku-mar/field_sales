<template>
  <div>
    <PageHeader :title="name" :status="d.name ? orderStatus(d) : null">
      <template #actions>
        <Dropdown v-if="d.name" :options="moreActions" align="end">
          <Button variant="ghost" icon="lucide-ellipsis" aria-label="More actions" />
        </Dropdown>
        <Button
          v-if="d.name"
          variant="ghost"
          label="Preview"
          icon-left="lucide-eye"
          :href="`/printview?doctype=Field%20Order&name=${encodeURIComponent(name)}`"
          target="_blank"
        />
        <template v-if="editable">
          <Button v-if="dirty" label="Save" variant="solid" :loading="saving" @click="save" />
          <Button v-else label="Submit" variant="solid" :loading="saving" @click="confirmSubmit" />
        </template>
      </template>
    </PageHeader>

    <div v-if="doc.get.error" class="px-5"><div class="fs-card"><EmptyState icon="lucide-circle-alert" title="Order not found" :description="errorText" /></div></div>

    <div v-else-if="!form" class="flex justify-center py-16 text-ink-gray-5"><LoadingIndicator class="size-5" /></div>

    <div v-else class="grid grid-cols-1 items-start gap-4 px-5 pb-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <!-- Order -->
      <div class="fs-card">
        <div class="grid grid-cols-1 gap-4 border-b border-desk-border p-5 sm:grid-cols-3">
          <Field label="Retailer" required>
            <LinkField
              v-if="editable"
              v-model="form.retailer"
              doctype="Retailer"
              title-field="retailer_name"
              description-field="city"
              :search-fields="['name', 'retailer_name', 'city', 'phone']"
              :filters="{ status: 'Active' }"
              :label="form.retailer_name"
              placeholder="Select retailer"
              @select="onRetailer"
            />
            <ReadOnly v-else :value="form.retailer_name || form.retailer" :aside="retailerCity.data?.city" :route="{ name: 'Retailer', params: { name: form.retailer } }" />
          </Field>
          <Field label="Visit">
            <LinkField
              v-if="editable"
              :key="form.retailer"
              v-model="form.visit"
              doctype="Visit"
              description-field="visit_date"
              :filters="form.retailer ? { retailer: form.retailer } : {}"
              placeholder="Select visit"
            />
            <ReadOnly v-else :value="form.visit" :route="form.visit && { name: 'Visit', params: { name: form.visit } }" />
          </Field>
          <Field label="Date" required>
            <DatePicker v-if="editable" v-model="form.order_date" />
            <ReadOnly v-else :value="formatDate(form.order_date)" />
          </Field>
        </div>

        <!-- Items -->
        <div class="space-y-3 border-b border-desk-border p-5">
          <div class="flex items-center gap-2">
            <h2 class="text-base font-semibold text-ink-gray-9">Items</h2>
            <span class="text-ink-gray-5">{{ form.items.length }}</span>
            <Button
              v-if="editable && selected.size"
              class="ml-auto"
              theme="red"
              :label="`Delete ${selected.size} ${selected.size === 1 ? 'row' : 'rows'}`"
              icon-left="lucide-trash-2"
              @click="deleteSelected"
            />
          </div>

          <div v-if="warningRows.length" class="flex items-start gap-2 rounded-4 bg-surface-red-2 px-3 py-2.5 text-ink-red-6">
            <span class="lucide-triangle-alert mt-px size-4 shrink-0" />
            <span>
              <b>{{ warningRows.length }} {{ warningRows.length === 1 ? 'item exceeds' : 'items exceed' }} available stock</b>
              <template v-if="syncInfo.data?.location_name"> at {{ syncInfo.data.location_name }}</template>.
              {{ editable ? 'Reduce the quantity or split the order before submitting.' : 'Cancel the order and amend it with lower quantities.' }}
            </span>
          </div>

          <div class="overflow-x-auto rounded-4 border border-outline-gray-2">
            <table class="w-full min-w-[720px] border-collapse text-left">
              <thead class="bg-surface-gray-1 text-ink-gray-5">
                <tr>
                  <th class="w-10 border-b border-outline-gray-2 px-3 py-2">
                    <Checkbox
                      v-if="editable"
                      :model-value="allSelected"
                      :indeterminate="selected.size > 0 && !allSelected"
                      aria-label="Select all rows"
                      @update:model-value="toggleAll"
                    />
                  </th>
                  <th class="cell-head">Product</th>
                  <th class="cell-head w-40">SKU</th>
                  <th class="cell-head w-32 whitespace-nowrap text-right">Available Qty</th>
                  <th class="cell-head w-24 text-right">Qty</th>
                  <th class="cell-head w-32 text-right">Rate</th>
                  <th class="cell-head w-32 border-r-0 text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!form.items.length">
                  <td colspan="7" class="px-3 py-6 text-center text-ink-gray-5">No items yet</td>
                </tr>
                <tr
                  v-for="row in form.items"
                  :key="row._key"
                  class="border-b border-outline-gray-2 last:border-b-0"
                  :class="isShort(row) ? 'bg-surface-red-1' : 'hover:bg-surface-gray-1'"
                >
                  <td class="px-3 py-1.5">
                    <Checkbox
                      v-if="editable"
                      :model-value="selected.has(row._key)"
                      :aria-label="`Select row ${rowLabel(row)}`"
                      @update:model-value="toggleRow(row._key)"
                    />
                  </td>
                  <td class="cell py-1">
                    <LinkField
                      v-if="editable"
                      v-model="row.product"
                      class="w-full"
                      variant="ghost"
                      doctype="Platform Product"
                      :label-of="productLabel"
                      description-field="sku"
                      :fields="PRODUCT_FIELDS"
                      :search-fields="['product_title', 'variant_title', 'sku', 'barcode']"
                      :filters="{ status: 'Active' }"
                      :exclude="productNames"
                      :label="productLabel(row)"
                      placeholder="Select product"
                      @select="(p) => setProduct(row, p)"
                    />
                    <div v-else class="text-ink-gray-9">{{ productLabel(row) }}</div>
                    <div v-if="isShort(row)" class="mt-0.5 flex items-center gap-1 text-xs text-ink-red-6">
                      <span class="lucide-triangle-alert size-3" />
                      Exceeds stock — only {{ formatNumber(row.available_qty) }} available
                    </div>
                  </td>
                  <td class="cell font-mono text-xs text-ink-gray-6">{{ row.sku || '—' }}</td>
                  <td class="cell text-right tabular-nums">
                    <Tooltip :text="tracks(row) ? null : 'Stock is not tracked for this product'" :disabled="tracks(row)">
                      <span :class="!tracks(row) && 'text-ink-gray-4'">{{ tracks(row) ? formatNumber(row.available_qty) : '—' }}</span>
                    </Tooltip>
                  </td>
                  <td class="cell p-0 text-right" :class="isShort(row) && 'outline outline-1 -outline-offset-1 outline-[--ink-red-5]'">
                    <input
                      v-if="editable"
                      v-model.number="row.qty"
                      type="number"
                      min="1"
                      class="num-input"
                      :class="isShort(row) && 'text-ink-red-6'"
                      :aria-label="`Quantity for ${rowLabel(row)}`"
                    />
                    <span v-else class="block px-3 tabular-nums" :class="isShort(row) && 'text-ink-red-6'">{{ formatNumber(row.qty, 3) }}</span>
                  </td>
                  <td class="cell p-0 text-right">
                    <input
                      v-if="editable"
                      v-model.number="row.rate"
                      type="number"
                      min="0"
                      step="0.01"
                      class="num-input"
                      :aria-label="`Rate for ${rowLabel(row)}`"
                    />
                    <span v-else class="block px-3 tabular-nums">{{ formatCurrency(row.rate) }}</span>
                  </td>
                  <td class="cell border-r-0 text-right tabular-nums">{{ formatCurrency(amountOf(row)) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="editable" class="flex gap-2">
            <Button label="Add Row" icon-left="lucide-plus" @click="addRow()" />
            <Button label="Add Multiple" @click="pickerOpen = true" />
          </div>
        </div>

        <!-- Totals -->
        <div class="p-5">
          <h2 class="mb-3 text-base font-semibold text-ink-gray-9">Totals</h2>
          <div class="grid grid-cols-1 gap-6 sm:grid-cols-2">
            <Field label="Total Quantity">
              <div class="flex h-7 items-center rounded-4 bg-surface-gray-2 px-2 tabular-nums text-ink-gray-8">
                {{ formatNumber(totals.qty, 3) }}
              </div>
            </Field>
            <dl class="space-y-2.5">
              <div class="flex justify-between"><dt class="text-ink-gray-6">Net Total</dt><dd class="tabular-nums">{{ formatCurrency(totals.net) }}</dd></div>
              <div class="flex justify-between"><dt class="text-ink-gray-6">GST {{ formatNumber(form.tax_percent, 2) }}%</dt><dd class="tabular-nums">{{ formatCurrency(totals.tax) }}</dd></div>
              <div class="flex justify-between"><dt class="text-ink-gray-6">Discount</dt><dd class="tabular-nums">{{ formatCurrency(form.discount_amount || 0) }}</dd></div>
              <div class="flex justify-between border-t border-outline-gray-2 pt-3 text-lg font-semibold text-ink-gray-9">
                <dt>Grand Total</dt><dd class="tabular-nums">{{ formatCurrency(totals.grand) }}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>

      <!-- Store sync -->
      <div class="fs-card">
        <div class="flex items-center gap-2 border-b border-desk-border px-4 py-3">
          <span class="lucide-shopping-bag size-4 text-ink-gray-7" />
          <h2 class="flex-1 text-base font-semibold text-ink-gray-9">{{ platform }} Sync</h2>
          <StatusPill :status="orderStatus(d)" />
        </div>
        <dl class="divide-y divide-outline-gray-1 px-4">
          <div v-for="item in syncRows" :key="item.label" class="flex items-center justify-between gap-3 py-2.5">
            <dt class="text-ink-gray-6">{{ item.label }}</dt>
            <dd class="truncate text-right" :class="item.muted ? 'text-ink-gray-5' : 'text-ink-gray-8'">
              <a v-if="item.href" :href="item.href" target="_blank" class="inline-flex items-center gap-1 text-ink-blue-6 hover:underline">
                {{ item.value }}<span class="lucide-external-link size-3.5" />
              </a>
              <template v-else>{{ item.value }}</template>
            </dd>
          </div>
        </dl>
        <div class="space-y-2 p-4 pt-1">
          <p v-if="d.docstatus === 0" class="text-ink-gray-5">The order is sent to the store when it is submitted.</p>
          <div v-if="d.docstatus === 1 && d.sync_status === 'Failed'" class="flex gap-2 rounded-4 bg-surface-red-2 px-3 py-2.5 text-ink-red-6">
            <span class="lucide-circle-alert mt-px size-4 shrink-0" />
            <div class="min-w-0 break-words">
              <div class="font-semibold">Sync failed</div>
              <div v-if="d.sync_error">{{ d.sync_error }}</div>
            </div>
          </div>
          <Button
            v-if="d.docstatus === 1 && d.sync_status === 'Failed'"
            class="w-full"
            variant="solid"
            label="Retry Sync"
            icon-left="lucide-refresh-cw"
            :loading="retrying"
            @click="retrySync"
          />
          <Button
            v-if="session.isManager && d.docstatus !== 0"
            class="w-full"
            label="View Sync Log"
            :route="{ name: 'SyncLogs', query: { reference: name } }"
          />
        </div>
      </div>
    </div>

    <!-- Add Multiple -->
    <Dialog v-model="pickerOpen" title="Add products" size="xl" @after-leave="resetPicker">
      <div class="space-y-3">
        <TextInput v-model="pickerQuery" placeholder="Search by name, SKU or barcode" autofocus>
          <template #prefix><span class="lucide-search size-4 text-ink-gray-5" /></template>
        </TextInput>
        <div class="max-h-80 overflow-y-auto rounded-4 border border-outline-gray-2">
          <div v-if="picker.loading && !picker.data" class="flex justify-center py-8"><LoadingIndicator class="size-4" /></div>
          <div v-else-if="!pickerRows.length" class="py-8 text-center text-ink-gray-5">No products found</div>
          <label
            v-for="p in pickerRows"
            :key="p.name"
            class="flex cursor-pointer items-center gap-3 border-b border-outline-gray-1 px-3 py-2 last:border-b-0 hover:bg-surface-gray-1"
          >
            <Checkbox :model-value="picked.has(p.name)" @update:model-value="togglePicked(p.name)" />
            <span class="min-w-0 flex-1">
              <span class="block truncate text-ink-gray-9">{{ productLabel(p) }}</span>
              <span class="font-mono text-xs text-ink-gray-5">{{ p.sku || p.name }}</span>
            </span>
            <span class="text-right text-xs tabular-nums text-ink-gray-5">
              {{ formatCurrency(p.price) }}
              <span class="block">{{ p.track_inventory ? `${formatNumber(p.available_qty)} in stock` : 'Not tracked' }}</span>
            </span>
          </label>
        </div>
      </div>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" @click="pickerOpen = false" />
          <Button variant="solid" :label="picked.size ? `Add ${picked.size}` : 'Add'" :disabled="!picked.size" @click="addPicked" />
        </div>
      </template>
    </Dialog>

    <!-- Confirmations -->
    <Dialog v-model="confirm.open" :title="confirm.title" :message="confirm.message" :icon="confirm.icon" :theme="confirm.theme">
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Not now" @click="confirm.open = false" />
          <Button variant="solid" :theme="confirm.theme" :label="confirm.label" :loading="confirm.busy" @click="runConfirm" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRouter } from 'vue-router'
import {
  Button,
  Checkbox,
  DatePicker,
  Dialog,
  Dropdown,
  LoadingIndicator,
  TextInput,
  Tooltip,
  createDocumentResource,
  createResource,
  debounce,
  toast,
} from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusPill from '@/components/StatusPill.vue'
import LinkField from '@/components/LinkField.vue'
import { useSessionStore } from '@/stores/session'
import { callMethod } from '@/utils/api'
import { formatCurrency, formatDate, formatNumber, formatTime, orderStatus, relativeDay } from '@/utils/format'

const props = defineProps({ name: { type: String, required: true } })
const router = useRouter()
const session = useSessionStore()

/** Label above a form control, with the desk's red asterisk for required fields. */
const Field = defineComponent({
  props: { label: String, required: Boolean },
  setup: (p, { slots }) => () =>
    h('div', { class: 'space-y-1.5' }, [
      h('div', { class: 'text-sm text-ink-gray-6' }, [p.label, p.required ? h('span', { class: 'text-ink-red-5' }, ' *') : null]),
      slots.default?.(),
    ]),
})

/** A saved value in the same grey box as an input, optionally linking to its page. */
const ReadOnly = defineComponent({
  props: { value: String, aside: String, route: Object },
  setup: (p) => () =>
    h('div', { class: 'flex h-7 items-center gap-2 rounded-4 bg-surface-gray-2 px-2' }, [
      p.value && p.route
        ? h(RouterLink, { to: p.route, class: 'flex-1 truncate text-ink-gray-8 hover:underline' }, () => p.value)
        : h('span', { class: ['flex-1 truncate', p.value ? 'text-ink-gray-8' : 'text-ink-gray-4'] }, p.value || '—'),
      p.aside ? h('span', { class: 'shrink-0 text-ink-gray-5' }, p.aside) : null,
    ]),
})

const PRODUCT_FIELDS = ['product_title', 'variant_title', 'sku', 'available_qty', 'price', 'track_inventory', 'external_variant_id']

// ---------------------------------------------------------------- document

const doc = createDocumentResource({
  doctype: 'Field Order',
  name: props.name,
  auto: true,
  onSuccess: () => resetForm(),
})
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))
const editable = computed(() => d.value.docstatus === 0)

// The editable copy. `_key` identifies rows in the template, including unsaved ones.
const form = ref(null)
let keySeq = 0
const withKey = (row) => ({ ...row, _key: row.name || `new-${++keySeq}` })

function resetForm() {
  form.value = { ...doc.doc, items: (doc.doc.items || []).map(withKey) }
  selected.value = new Set()
}

// Compares only what the user can change, so loading or recomputed fields never count as edits.
const editableState = (o) =>
  o && JSON.stringify([o.retailer, o.visit || null, o.order_date, (o.items || []).map((r) => [r.product || null, +r.qty || 0, +r.rate || 0])])
const dirty = computed(() => !!form.value && editable.value && editableState(form.value) !== editableState(doc.doc))

onBeforeRouteLeave(() => !dirty.value || window.confirm('You have unsaved changes to this order. Leave without saving?'))

// The city shown beside the retailer on a submitted order.
const retailerCity = createResource({
  url: 'frappe.client.get_value',
  makeParams: () => ({ doctype: 'Retailer', filters: form.value.retailer, fieldname: 'city' }),
})
watch(
  () => !editable.value && form.value?.retailer,
  (retailer) => retailer && retailerCity.fetch(),
)

function onRetailer(retailer) {
  form.value.retailer_name = retailer.retailer_name
  form.value.visit = null
}

// ---------------------------------------------------------------- items and stock

// track_inventory is on the product, not the row: fetched for every product on the order.
const tracked = ref({})
const productNames = computed(() => (form.value?.items || []).map((r) => r.product).filter(Boolean))
const trackedLookup = createResource({
  url: 'frappe.client.get_list',
  makeParams: (names) => ({
    doctype: 'Platform Product',
    filters: { name: ['in', names] },
    fields: ['name', 'track_inventory'],
    limit_page_length: 0,
  }),
  onSuccess: (rows) => rows.forEach((p) => (tracked.value[p.name] = !!p.track_inventory)),
})
watch(
  productNames,
  (names) => {
    const missing = names.filter((n) => !(n in tracked.value))
    if (missing.length) trackedLookup.submit(missing)
  },
  { immediate: true },
)

const tracks = (row) => !!tracked.value[row.product]
const isShort = (row) => tracks(row) && (+row.qty || 0) > (+row.available_qty || 0)
const amountOf = (row) => Math.round((+row.qty || 0) * (+row.rate || 0) * 100) / 100
const warningRows = computed(() => (form.value?.items || []).filter(isShort))

const productLabel = (p) => [p.product_title || p.product, p.variant_title].filter(Boolean).join(' · ')
const rowLabel = (row) => productLabel(row) || `row ${form.value.items.indexOf(row) + 1}`

// Saved totals come from the server. While editing, they are estimated the way
// FieldOrder._compute_totals works; the server's rounding can differ by a paisa.
const totals = computed(() => {
  if (!dirty.value) {
    const o = d.value
    return { qty: o.total_qty, net: o.net_total, tax: o.tax_amount, grand: o.grand_total }
  }
  const items = form.value.items
  const net = Math.round(items.reduce((sum, r) => sum + amountOf(r), 0) * 100) / 100
  const tax = Math.round(((net * (+form.value.tax_percent || 0)) / 100) * 100) / 100
  return {
    qty: items.reduce((sum, r) => sum + (+r.qty || 0), 0),
    net,
    tax,
    grand: Math.round((net + tax - (+form.value.discount_amount || 0)) * 100) / 100,
  }
})

function setProduct(row, p) {
  tracked.value[p.name] = !!p.track_inventory
  Object.assign(row, {
    product: p.name,
    product_title: p.product_title,
    variant_title: p.variant_title,
    sku: p.sku,
    available_qty: p.available_qty,
    rate: p.price,
    external_variant_id: p.external_variant_id,
  })
}

function addRow(product) {
  const row = withKey({ doctype: 'Field Order Item', product: null, qty: 1, rate: 0 })
  if (product) setProduct(row, product)
  form.value.items.push(row)
}

const selected = ref(new Set())
const allSelected = computed(() => !!form.value?.items.length && selected.value.size === form.value.items.length)
function toggleRow(key) {
  const next = new Set(selected.value)
  next.has(key) ? next.delete(key) : next.add(key)
  selected.value = next
}
function toggleAll(on) {
  selected.value = new Set(on ? form.value.items.map((r) => r._key) : [])
}
function deleteSelected() {
  form.value.items = form.value.items.filter((r) => !selected.value.has(r._key))
  selected.value = new Set()
}

// ---------------------------------------------------------------- Add Multiple

const pickerOpen = ref(false)
const pickerQuery = ref('')
const picked = ref(new Set())
const picker = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => {
    const txt = pickerQuery.value.trim()
    return {
      doctype: 'Platform Product',
      fields: ['name', ...PRODUCT_FIELDS],
      filters: { status: 'Active' },
      or_filters: txt ? ['product_title', 'variant_title', 'sku', 'barcode'].map((f) => [f, 'like', `%${txt}%`]) : null,
      order_by: 'product_title asc',
      limit_page_length: 50,
    }
  },
})
const pickerRows = computed(() => (picker.data || []).filter((p) => !productNames.value.includes(p.name)))
watch(pickerOpen, (open) => open && picker.reload())
watch(pickerQuery, debounce(() => picker.reload(), 250))

function togglePicked(name) {
  const next = new Set(picked.value)
  next.has(name) ? next.delete(name) : next.add(name)
  picked.value = next
}
function addPicked() {
  // An empty row left by Add Row is filled first rather than left dangling.
  form.value.items = form.value.items.filter((r) => r.product)
  ;(picker.data || []).filter((p) => picked.value.has(p.name)).forEach((p) => addRow(p))
  pickerOpen.value = false
}
function resetPicker() {
  pickerQuery.value = ''
  picked.value = new Set()
}

// ---------------------------------------------------------------- actions

const saving = ref(false)

function validateForm() {
  const f = form.value
  const problem =
    (!f.retailer && 'Select a retailer') ||
    (!f.order_date && 'Set the order date') ||
    (!f.items.length && 'Add at least one item') ||
    (f.items.some((r) => !r.product) && 'Select a product in every row, or delete the empty rows') ||
    (f.items.some((r) => !(+r.qty > 0)) && 'Every quantity must be greater than 0')
  if (problem) toast.error(problem)
  return !problem
}

async function save() {
  if (!validateForm()) return false
  saving.value = true
  try {
    const items = form.value.items.map(({ _key, ...row }) => row)
    await callMethod('frappe.client.save', { doc: { ...form.value, items } })
    await doc.reload()
    toast.success('Order saved')
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

const confirm = reactive({ open: false, title: '', message: '', label: '', icon: null, theme: 'gray', busy: false, action: null })
function ask(options) {
  Object.assign(confirm, { icon: null, theme: 'gray', ...options, open: true, busy: false })
}
async function runConfirm() {
  confirm.busy = true
  try {
    await confirm.action()
    confirm.open = false
  } catch {
    // callMethod already showed the error
  } finally {
    confirm.busy = false
  }
}

function confirmSubmit() {
  if (warningRows.value.length) {
    toast.error('Reduce the quantities that exceed available stock before submitting')
    return
  }
  ask({
    title: 'Submit order?',
    message: `${props.name} will be sent to the store for ${formatCurrency(totals.value.grand)}. A submitted order can no longer be edited.`,
    label: 'Submit',
    action: async () => {
      await callMethod('frappe.client.submit', { doc: doc.doc })
      await doc.reload()
      toast.success('Order submitted; syncing to the store')
    },
  })
}

async function refreshStock() {
  if (dirty.value && !(await save())) return
  await callMethod('field_sales.api.refresh_stock', { field_order: props.name })
  await doc.reload()
  toast.success('Stock updated from the store')
}

const moreActions = computed(() => {
  const desk = `/app/field-order/${encodeURIComponent(props.name)}`
  const actions = []
  if (editable.value) {
    actions.push({ label: 'Refresh Stock', icon: 'lucide-refresh-ccw', onClick: refreshStock })
  }
  if (storeUrl.value) {
    actions.push({ label: `Open in ${platform.value}`, icon: 'lucide-external-link', onClick: () => window.open(storeUrl.value, '_blank') })
  }
  actions.push({
    label: d.value.docstatus === 2 ? 'Amend in Desk' : 'Open in Desk',
    icon: 'lucide-app-window',
    onClick: () => (window.location.href = desk),
  })
  if (d.value.docstatus === 1) {
    actions.push({
      label: 'Cancel Order',
      icon: 'lucide-circle-x',
      theme: 'red',
      onClick: () =>
        ask({
          title: 'Cancel order?',
          message: `${props.name} stays in Field Sales as cancelled. An order already created in the store is not cancelled there.`,
          label: 'Cancel Order',
          icon: 'lucide-circle-x',
          theme: 'red',
          action: async () => {
            await callMethod('frappe.client.cancel', { doctype: 'Field Order', name: props.name })
            await doc.reload()
            toast.success('Order cancelled')
          },
        }),
    })
  }
  if (d.value.docstatus !== 1) {
    actions.push({
      label: 'Delete',
      icon: 'lucide-trash-2',
      theme: 'red',
      onClick: () =>
        ask({
          title: 'Delete order?',
          message: `${props.name} will be deleted permanently.`,
          label: 'Delete',
          icon: 'lucide-trash-2',
          theme: 'red',
          action: async () => {
            await callMethod('frappe.client.delete', { doctype: 'Field Order', name: props.name })
            form.value = null // nothing left to save; skip the unsaved-changes prompt
            toast.success('Order deleted')
            router.push({ name: 'Orders' })
          },
        }),
    })
  }
  return actions
})

// ---------------------------------------------------------------- store sync

const syncInfo = createResource({
  url: 'field_sales.frontend_api.get_order_sync_info',
  params: { field_order: props.name },
  auto: true,
})
// Refetch once the sync writes new IDs, so the store link appears.
watch(
  () => [d.value.external_order_id, d.value.external_draft_id],
  (now, before) => before && now.join() !== before.join() && syncInfo.reload(),
)

const platform = computed(() => d.value.platform || session.store.platform || 'Store')
const storeUrl = computed(() => syncInfo.data?.store_url)

const syncRows = computed(() => {
  const o = d.value
  const attempts = o.sync_attempts || 0
  const max = syncInfo.data?.max_attempts
  return [
    {
      label: 'Draft Order ID',
      value: o.external_draft_id ? `#${o.external_draft_id}` : 'Not created',
      muted: !o.external_draft_id,
      href: !o.external_order_id && o.external_draft_id ? storeUrl.value : null,
    },
    {
      label: 'Order ID',
      value: o.external_order_id ? `#${o.external_order_id}` : 'Not created',
      muted: !o.external_order_id,
      href: o.external_order_id ? storeUrl.value : null,
    },
    { label: 'Location', value: syncInfo.data?.location_name || '—', muted: !syncInfo.data?.location_name },
    {
      label: 'Last Attempt',
      value: o.last_sync_attempt ? `${relativeDay(o.last_sync_attempt)}, ${formatTime(o.last_sync_attempt)}` : 'Never',
      muted: !o.last_sync_attempt,
    },
    { label: 'Attempts', value: max ? `${attempts} of ${max}` : attempts },
  ]
})

const retrying = ref(false)
async function retrySync() {
  retrying.value = true
  try {
    const r = await callMethod('field_sales.api.retry_sync', { field_order: props.name })
    toast.success(r.message)
    setTimeout(() => doc.reload(), 4000)
  } finally {
    retrying.value = false
  }
}
</script>

<style scoped>
.cell-head {
  @apply border-b border-r border-outline-gray-2 px-3 py-2 font-normal;
}
.cell {
  @apply border-r border-outline-gray-2 px-3 py-1.5;
}
.num-input {
  @apply h-9 w-full border-0 bg-transparent px-3 text-right tabular-nums focus:bg-surface-base focus:outline-none;
  appearance: textfield;
}
.num-input:focus {
  box-shadow: inset 0 0 0 1px var(--outline-gray-4);
}
.num-input::-webkit-inner-spin-button,
.num-input::-webkit-outer-spin-button {
  appearance: none;
}
</style>
