<template>
  <div v-if="doc.get.loading && !doc.doc" class="fs-card flex justify-center py-16 text-ink-gray-5">
    <LoadingIndicator class="size-5" />
  </div>

  <div v-else-if="doc.get.error || !doc.doc" class="fs-card">
    <EmptyState icon="lucide-circle-alert" title="Could not load Store Connection" :description="errorText">
    </EmptyState>
  </div>

  <div v-else class="space-y-4">
    <!-- Outcome of Test Connection on this visit, with the scopes the store granted the app -->
    <div
      v-if="testBanner"
      role="status"
      class="flex items-start gap-2.5 rounded-3 border px-4 py-3 text-sm"
      :class="testBanner.ok ? 'border-outline-green-2 bg-surface-green-1 text-ink-green-7' : 'border-outline-red-2 bg-surface-red-1 text-ink-red-6'"
    >
      <span :class="[testBanner.ok ? 'lucide-circle-check' : 'lucide-circle-x', 'mt-0.5 size-4 shrink-0']" aria-hidden="true" />
      <p class="min-w-0 flex-1">
        <span class="font-semibold">{{ testBanner.title }}</span>
        {{ testBanner.text }}
      </p>
      <button
        type="button"
        class="flex shrink-0 rounded-3 p-0.5 opacity-70 hover:opacity-100"
        aria-label="Dismiss"
        @click="testBanner = null"
      >
        <span class="lucide-x size-4" />
      </button>
    </div>

    <div class="fs-card">
      <!-- Inside the Shopify admin the install owns the connection: no platform, URL or credentials -->
      <section v-if="embedded" class="p-8">
        <h3 class="text-base font-medium text-ink-gray-9">Connected shop</h3>
        <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <TextInput :model-value="d.shop_name ? `${d.shop_name} (${d.store_url})` : d.store_url" label="Shop" disabled />
          <TextInput :model-value="formatDateTime(d.installed_on)" label="Installed On" disabled />
          <TextInput
            class="md:col-span-2"
            :model-value="(d.shopify_scopes || '').split(',').join(', ')"
            label="Granted Access"
            disabled
          />
        </div>
      </section>
      <!-- Connection -->
      <section v-if="!embedded" class="p-8">
        <h3 class="text-base font-medium text-ink-gray-9">Connection</h3>
        <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <Select v-model="d.platform" :options="PLATFORMS" label="Platform" required placeholder="Select platform" />
          <TextInput
            v-model="d.store_url"
            label="Shop Domain"
            placeholder="kovai-mart.myshopify.com"
            required
            description="Shopify: kovai-mart.myshopify.com · WooCommerce: https://shop.com · Odoo: https://company.odoo.com"
          />
          <Checkbox
            v-model="d.enabled"
            label="Enabled"
            description="Sync Field Orders and Customers to the store"
          />
        </div>
      </section>

      <!-- Credentials -->
      <section v-if="!embedded" class="border-t border-desk-border p-4">
        <div class="flex items-center justify-between gap-2">
          <h3 class="text-base font-medium text-ink-gray-9">Credentials</h3>
          <span class="inline-flex items-center gap-1.5 text-xs text-ink-gray-5">
            <span class="lucide-lock size-3.5" />
            Stored encrypted
          </span>
        </div>

        <!-- Shopify -->
        <div v-if="d.platform === 'Shopify'" class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <TextInput v-model="d.shopify_client_id" label="Client ID" required>
            <template #suffix>
              <button
                type="button"
                class="flex size-5 items-center justify-center rounded text-ink-gray-5 hover:text-ink-gray-8"
                aria-label="Copy Client ID"
                title="Copy"
                @click="copyClientId"
              >
                <span class="lucide-copy size-3.5" />
              </button>
            </template>
          </TextInput>
          <TextInput
            v-model="d.shopify_client_secret"
            :type="secretsShown.shopify_client_secret ? 'text' : 'password'"
            label="Client Secret"
            required
            description="Stored encrypted — the masked value is kept unless you type a new secret"
          >
            <template #suffix><SecretToggle field="shopify_client_secret" /></template>
          </TextInput>
          <TextInput :model-value="formatDateTime(d.token_expiry)" label="Token Expiry" disabled :description="tokenHint" />
          <Select v-model="d.shopify_api_version" :options="apiVersions" label="API Version" />
        </div>

        <!-- WooCommerce -->
        <div v-else-if="d.platform === 'WooCommerce'" class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <TextInput v-model="d.woo_consumer_key" label="Consumer Key" required />
          <TextInput
            v-model="d.woo_consumer_secret"
            :type="secretsShown.woo_consumer_secret ? 'text' : 'password'"
            label="Consumer Secret"
            required
            description="Stored encrypted — the masked value is kept unless you type a new secret"
          >
            <template #suffix><SecretToggle field="woo_consumer_secret" /></template>
          </TextInput>
          <TextInput v-model="d.woo_api_version" label="API Version" placeholder="wc/v3" />
        </div>

        <!-- Odoo -->
        <div v-else-if="d.platform === 'Odoo'" class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <TextInput v-model="d.odoo_database" label="Database" required />
          <TextInput v-model="d.odoo_username" label="Username" required />
          <TextInput
            v-model="d.odoo_api_key"
            :type="secretsShown.odoo_api_key ? 'text' : 'password'"
            label="API Key"
            required
            description="Stored encrypted — the masked value is kept unless you type a new key"
          >
            <template #suffix><SecretToggle field="odoo_api_key" /></template>
          </TextInput>
        </div>

        <div class="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button
            variant="outline"
            label="Test Connection"
            icon-left="lucide-cable"
            :loading="testing"
            :disabled="d.platform !== 'Shopify'"
            @click="testConnection"
          />
          <p v-if="d.platform && d.platform !== 'Shopify'" class="text-sm text-ink-gray-5">
            Live calls support Shopify connections only (for now)
          </p>
          <p v-else-if="status" class="inline-flex items-center gap-1.5 text-sm">
            <span
              :class="[status.ok ? 'lucide-circle-check text-ink-green-7' : 'lucide-circle-x text-ink-red-5', 'size-4 shrink-0']"
            />
            <span :class="status.ok ? 'text-ink-gray-6' : 'text-ink-red-5'">{{ status.text }}</span>
          </p>
        </div>
      </section>

      <!-- Defaults -->
      <section class="border-t border-desk-border p-4">
        <h3 class="text-base font-medium text-ink-gray-9">Defaults</h3>
        <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <Select
            v-if="d.platform === 'Shopify'"
            :model-value="d.default_location_id"
            :options="locationOptions"
            label="Default Location"
            required
            description="Stock is checked against this Shopify location unless an order picks another"
            @update:model-value="setLocation"
          />
          <TextInput
            v-else
            v-model="d.default_location_id"
            label="Default Location ID"
            description="Odoo warehouse ID; leave blank for WooCommerce"
          />
          <TextInput :model-value="d.default_location_name" label="Default Location Name" disabled />
          <Select v-model="d.order_mode" :options="ORDER_MODES" label="Create Shopify Order As" />
          <Select v-model="d.product_sync_frequency" :options="SYNC_FREQUENCIES" label="Product Sync Frequency" />
        </div>
      </section>

      <!-- Locations: the places reps can sell from. Saved with the form. -->
      <section class="border-t border-desk-border p-4">
        <h3 class="text-base font-medium text-ink-gray-9">Locations</h3>
        <p class="mt-1 text-sm text-ink-gray-5">
          Reps pick one of the ticked locations on each order, and its stock is kept in sync. Use Fetch Locations to
          load them from the store.
        </p>
        <div v-if="!locationRows.length" class="mt-4 text-sm text-ink-gray-5">No locations yet</div>
        <div v-else class="mt-4 overflow-x-auto rounded-3 border border-desk-border">
          <table class="w-full text-left">
            <thead>
              <tr class="border-b border-desk-border bg-surface-gray-1 text-xs font-medium text-ink-gray-5">
                <th class="px-4 py-2">Name</th>
                <th class="px-4 py-2">ID</th>
                <th class="px-4 py-2 text-right">Sell from here</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in locationRows" :key="row.location_id" class="border-b border-desk-border last:border-0">
                <td class="px-4 py-2.5 text-ink-gray-9">
                  {{ row.location_name || row.location_id }}
                  <span
                    v-if="row.location_id === d.default_location_id"
                    class="ml-2 rounded-3 bg-surface-gray-2 px-1.5 py-0.5 text-xs text-ink-gray-7"
                  >Default</span>
                  <span v-else-if="missingFromStore(row)" class="ml-2 text-xs text-ink-amber-7">Not returned by the store</span>
                </td>
                <td class="px-4 py-2.5 font-mono text-xs text-ink-gray-6">{{ row.location_id }}</td>
                <td class="px-4 py-2.5">
                  <div class="flex justify-end">
                    <Checkbox
                      :model-value="!!row.sell_from"
                      :disabled="row.location_id === d.default_location_id"
                      :aria-label="`Sell from ${row.location_name || row.location_id}`"
                      @update:model-value="(on) => (row.sell_from = on ? 1 : 0)"
                    />
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Status -->
      <section v-if="!embedded" class="border-t border-desk-border p-4">
        <h3 class="text-base font-medium text-ink-gray-9">Status</h3>
        <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <TextInput :model-value="d.shop_name" label="Shop Name" disabled />
          <TextInput :model-value="d.last_test_status" label="Last Test Status" disabled />
          <TextInput :model-value="formatDateTime(d.last_tested_on)" label="Last Tested On" disabled />
        </div>
      </section>

      <!-- Opened by the header's Fetch Locations button; picking one and saving
           mirrors the desk's Select Default Location dialog. -->
      <Dialog v-model="locationDialog.open" title="Select Default Location">
        <div class="space-y-3">
          <Select
            v-model="locationDialog.picked"
            :options="locationOptions"
            label="Location"
            required
            placeholder="Select location"
          />
          <p class="text-sm text-ink-gray-5">Stock is checked against this Shopify location.</p>
        </div>
        <template #actions>
          <div class="flex justify-end gap-2">
            <Button label="Cancel" :disabled="locationDialog.busy" @click="locationDialog.open = false" />
            <Button
              variant="solid"
              label="Set Default"
              :loading="locationDialog.busy"
              :disabled="!locationDialog.picked"
              @click="setDefaultLocation"
            />
          </div>
        </template>
      </Dialog>
    </div>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, reactive, ref } from 'vue'
import {
  Button,
  Checkbox,
  Dialog,
  LoadingIndicator,
  Select,
  TextInput,
  createDocumentResource,
  createResource,
  toast,
} from 'frappe-ui'
import EmptyState from '@/components/EmptyState.vue'
import { callMethod } from '@/utils/api'
import { isEmbedded } from '@/shopify/embedded'
import { createMerchantStoreDoc, merchantLocationsUrl } from '@/shopify/merchantSettings'
import { daysAgo, formatDate, formatDateTime, formatTime } from '@/utils/format'

const embedded = isEmbedded()
// Store Connection is a Single; its name is the doctype itself. Inside the Shopify admin the
// merchant endpoints are used instead (only locations, defaults and similar can change there).
const doc = embedded
  ? createMerchantStoreDoc()
  : createDocumentResource({ doctype: 'Store Connection', name: 'Store Connection' })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))

// Eye toggle rendered inside each secret input's suffix.
const secretsShown = reactive({})
const SecretToggle = defineComponent({
  props: { field: { type: String, required: true } },
  setup(props) {
    return () =>
      h(
        'button',
        {
          type: 'button',
          class: 'flex size-5 items-center justify-center rounded text-ink-gray-5 hover:text-ink-gray-8',
          'aria-label': secretsShown[props.field] ? `Hide ${props.field}` : `Show ${props.field}`,
          title: secretsShown[props.field] ? 'Hide' : 'Show',
          onClick: () => (secretsShown[props.field] = !secretsShown[props.field]),
        },
        h('span', {
          class: secretsShown[props.field] ? 'lucide-eye-off size-3.5' : 'lucide-eye size-3.5',
        }),
      )
  },
})

const PLATFORMS = ['Shopify', 'WooCommerce', 'Odoo'].map((v) => ({ label: v, value: v }))
const ORDER_MODES = [
  { label: 'Draft Order', value: 'Draft' },
  { label: 'Confirmed Order', value: 'Confirmed' },
]
const SYNC_FREQUENCIES = ['Every 30 minutes', 'Hourly', 'Daily'].map((v) => ({ label: v, value: v }))
// Shopify ships a quarterly API version and supports about the last four.
const API_VERSIONS = ['2025-04', '2025-07', '2025-10', '2026-01', '2026-04', '2026-07', '2026-10']

const apiVersions = computed(() => {
  const current = d.value.shopify_api_version
  const versions = current && !API_VERSIONS.includes(current) ? [...API_VERSIONS, current].sort() : API_VERSIONS
  return versions.map((v) => ({ label: v, value: v }))
})

// The shop's locations, for the Default Location select and the Select Default
// Location dialog. Fetched only by the Fetch Locations button — never on page
// load (the store call is slow) — so before that the select shows just the
// stored location. Fetch errors surface as toasts from the button.
const locations = createResource({ url: embedded ? merchantLocationsUrl : 'field_sales.api.get_locations', method: embedded ? 'GET' : 'POST' })

const locationOptions = computed(() => {
  const options = (locations.data || []).map((l) => ({ label: l.name, value: l.id }))
  const current = d.value.default_location_id
  if (current && !options.some((o) => o.value === current)) {
    options.unshift({ label: d.value.default_location_name || String(current), value: current })
  }
  return options
})

// The header's Fetch Locations button (exposed for the page shell): fetches
// the store's locations, then opens the Select Default Location dialog.
const fetchingLocations = computed(() => locations.loading)

// Rows of the Locations table, default first. The default is always sold from.
const locationRows = computed(() =>
  [...(d.value.locations || [])].sort((a, b) => (b.location_id === d.value.default_location_id) - (a.location_id === d.value.default_location_id)),
)
// Rows the store no longer returns are kept (never deleted for you) but flagged after a fetch.
const missingFromStore = (row) => !!locations.data && !locations.data.some((l) => l.id === row.location_id)

// Merge the store's locations into the table: new ones start unticked (the default is ticked),
// names are refreshed, and existing ticks are kept.
function mergeLocations(fetched, defaultId) {
  if (!doc.doc.locations) doc.doc.locations = []
  for (const loc of fetched) {
    const row = doc.doc.locations.find((r) => r.location_id === loc.id)
    if (row) row.location_name = loc.name
    else doc.doc.locations.push({ doctype: 'Store Location', location_id: loc.id, location_name: loc.name, sell_from: loc.id === defaultId ? 1 : 0 })
  }
}
const locationDialog = reactive({ open: false, picked: null, busy: false })
async function fetchLocations() {
  try {
    const fetched = await locations.fetch()
    if (!fetched || !fetched.length) {
      toast.info('No active locations found on the store')
      return
    }
    mergeLocations(fetched, d.value.default_location_id) // shown (unsaved) in the Locations section
    locationDialog.picked = d.value.default_location_id || fetched[0].id
    locationDialog.open = true
  } catch (error) {
    toast.error(error?.messages?.[0] || error?.message || 'Could not fetch locations')
  }
}

async function setDefaultLocation() {
  locationDialog.busy = true
  try {
    mergeLocations(locations.data || [], locationDialog.picked)
    setLocation(locationDialog.picked)
    await doc.save.submit()
    locationDialog.open = false
    await doc.reload()
    toast.success('Default location set')
  } catch (error) {
    toast.error(error?.messages?.[0] || error?.message || 'Could not set the default location')
  } finally {
    locationDialog.busy = false
  }
}

// Green "Shop · time" pill beside the page title; survives reloads because
// test_connection persists the shop name. testResult needs no special case —
// the doc reload after a test refreshes these fields.
const titleStatus = computed(() => {
  if (!d.value.last_tested_on) return null
  const ok = d.value.last_test_status === 'Success'
  const when = daysAgo(d.value.last_tested_on) === 0 ? formatTime(d.value.last_tested_on) : formatDate(d.value.last_tested_on)
  if (ok) {
    return {
      status: 'Success',
      label: `${d.value.shop_name || 'Connected'} · ${when}`,
      icon: 'lucide-circle-check',
    }
  }
  return { status: 'Failed', label: `Failed · ${when}`, icon: 'lucide-circle-x' }
})

function setLocation(id) {
  const picked = locationOptions.value.find((o) => o.value === id)
  doc.doc.default_location_id = id
  doc.doc.default_location_name = picked?.label || ''
  // The default is always sold from (the server also makes sure of it).
  const row = (doc.doc.locations || []).find((r) => r.location_id === id)
  if (row) row.sell_from = 1
}

async function copyClientId() {
  try {
    await navigator.clipboard.writeText(d.value.shopify_client_id || '')
    toast.success('Client ID copied')
  } catch {
    toast.error('Could not copy to clipboard')
  }
}

const tokenHint = computed(() => {
  if (!d.value.token_expiry) return 'Not issued yet — fetched automatically on the next sync'
  const expiry = new Date(String(d.value.token_expiry).replace(' ', 'T'))
  const ms = expiry - Date.now()
  if (ms <= 0) return 'Expired — refreshed automatically on the next call'
  const hours = Math.floor(ms / 3600000)
  const minutes = Math.floor((ms % 3600000) / 60000)
  return `Expires in ${hours} h ${minutes} m — refreshed automatically`
})

const saving = ref(false)
async function save() {
  saving.value = true
  let saved = false
  try {
    await doc.save.submit()
    saved = true
    toast.success('Store Connection saved')
  } catch (error) {
    toast.error(error?.messages?.[0] || error?.message || 'Could not save Store Connection')
  } finally {
    saving.value = false
  }
  return saved
}

// Result of the Test Connection button on this visit; the doc's own
// last_test_status / last_tested_on cover the state before it.
const testResult = ref(null)
const status = computed(() => {
  if (testResult.value) return testResult.value
  if (!d.value.last_tested_on) return null
  const ok = d.value.last_test_status === 'Success'
  const when = formatDateTime(d.value.last_tested_on)
  return ok
    ? { ok, text: `Verified ${when}` }
    : { ok, text: `${d.value.last_test_status} · ${when}` }
})

// The banner above the form; only set by a test on this visit (scopes are not stored).
const testBanner = ref(null)

const testing = ref(false)
async function testConnection() {
  testing.value = true
  try {
    // The server reads saved settings, so unsaved edits are saved first (as in
    // the desk); if that save fails, the test would run stale settings.
    if (doc.isDirty && !(await save())) return
    const started = performance.now()
    const result = await callMethod('field_sales.api.test_connection')
    const elapsed = Math.max(1, Math.round(performance.now() - started))
    const when = formatDateTime(nowSql())
    testResult.value = result.ok
      ? { ok: true, text: `Connected to ${result.shop_name || 'the store'} · ${elapsed} ms · ${when}` }
      : { ok: false, text: `${result.message} · ${when}` }
    testBanner.value = result.ok
      ? {
          ok: true,
          title: 'Connection successful.',
          text: [
            `Shop: ${result.shop_name || '—'} (${result.shop_domain || d.value.store_url})`,
            result.scopes?.length ? `Scopes: ${result.scopes.join(', ')}` : 'Scopes: none reported',
          ].join(' · '),
        }
      : { ok: false, title: 'Connection failed.', text: result.message }
    if (result.ok) toast.success(result.message)
    else toast.error(result.message)
    // The test may have refreshed the access token; reload expiry and status.
    await doc.reload()
  } catch {
    // callMethod already toasted the error message.
  } finally {
    testing.value = false
  }
}

function nowSql() {
  const n = new Date()
  const p = (x) => String(x).padStart(2, '0')
  return `${n.getFullYear()}-${p(n.getMonth() + 1)}-${p(n.getDate())} ${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`
}

// The Settings page header renders Discard / Save Changes from these.
const isDirty = computed(() => !!doc.isDirty)
function discard() {
  doc.reload()
}
defineExpose({ fetchLocations, fetchingLocations, titleStatus, isDirty, saving, testing, save, discard })
</script>
