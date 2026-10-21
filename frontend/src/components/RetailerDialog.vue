<template>
  <Dialog v-model="open" :title="isEdit ? 'Edit Customer' : 'New Customer'" size="lg" @after-leave="resetForm">
    <div class="space-y-6">
      <!-- Details -->
      <section>
        <h3 class="text-base font-medium text-ink-gray-9">Details</h3>
        <div class="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
          <TextInput v-model="form.retailer_name" label="Customer Name" required placeholder="Kovai Mart" />
          <TextInput v-model="form.phone" label="Phone" required type="tel" placeholder="98765 43210" description="10 digits, optionally with +91" />
          <TextInput v-model="form.owner_name" label="Owner Name" />
          <TextInput v-model="form.email" label="Email" type="email" />
          <Combobox
            v-model="form.sales_rep"
            :options="repOptions"
            trigger="button"
            label="Sales Rep"
            placeholder="Select sales rep"
            empty-text="No sales reps found"
            description="Customers are visible through their Sales Rep"
          />
          <Select v-model="form.retailer_type" :options="RETAILER_TYPES" label="Customer Type" placeholder="Select type" />
          <TextInput v-model="form.gstin" label="GSTIN" />
          <Select v-model="form.status" :options="['Active', 'Inactive']" label="Status" />
        </div>
      </section>

      <!-- Address & Location -->
      <section class="border-t border-desk-border pt-6">
        <h3 class="text-base font-medium text-ink-gray-9">Address & Location</h3>
        <div class="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
          <TextInput v-model="form.address_line1" label="Address Line 1" required />
          <TextInput v-model="form.address_line2" label="Address Line 2" />
          <TextInput v-model="form.city" label="City" required />
          <TextInput v-model="form.state" label="State" />
          <TextInput v-model="form.pincode" label="Pincode" />
          <TextInput
            v-if="session.isManager"
            v-model="form.geofence_radius_m"
            label="Geofence Radius (m)"
            type="number"
            placeholder="50 (default)"
          />
          <TextInput v-model="form.latitude" label="Latitude" type="number" description="-90 to 90" :disabled="locationLocked" />
          <TextInput v-model="form.longitude" label="Longitude" type="number" description="-180 to 180" :disabled="locationLocked" />
        </div>
        <p v-if="locationLocked" class="mt-2 text-sm text-ink-gray-5">
          The location is set. Ask a Sales Manager to change it.
        </p>
        <div v-else class="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button
            variant="outline"
            label="Select Location"
            icon-left="lucide-map-pin-plus"
            @click="pickerOpen = true"
          />
          <Button
            variant="outline"
            label="Use My Location"
            icon-left="lucide-locate-fixed"
            :loading="locating"
            @click="useMyLocation"
          />
        </div>
        <p v-if="!locationLocked" class="mt-2 text-sm text-ink-gray-5">
          Pick on a map or capture your GPS position; otherwise coordinates are set automatically at the first visit check-in.
        </p>
      </section>
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="busy" @click="open = false" />
        <Button
          variant="solid"
          :label="isEdit ? 'Save' : 'Create'"
          :loading="busy"
          :disabled="!valid"
          @click="submit"
        />
      </div>
    </template>
  </Dialog>

  <!-- Map picker for the latitude/longitude fields. -->
  <MapPickerDialog
    v-model="pickerOpen"
    :latitude="form.latitude"
    :longitude="form.longitude"
    @select="applyPickedLocation"
  />
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Combobox, Dialog, Select, TextInput, createListResource, createResource, toast } from 'frappe-ui'
import { callMethod, getPosition } from '@/utils/api'
import MapPickerDialog from '@/components/MapPickerDialog.vue'
import { useSessionStore } from '@/stores/session'

const RETAILER_TYPES = ['Kirana', 'Supermarket', 'Wholesaler', 'Medical', 'Other']

const props = defineProps({
  /** Retailer name to edit; absent (or null) means create. */
  retailer: { type: String, default: null },
})
const emit = defineEmits(['updated'])

const open = defineModel({ type: Boolean, default: false })
const isEdit = computed(() => !!props.retailer)

const session = useSessionStore()
const router = useRouter()

// Options for the Sales Rep link: get_list applies the same permissions as the desk.
const reps = createListResource({
  doctype: 'Sales Rep',
  fields: ['name', 'full_name'],
  orderBy: 'full_name asc',
  pageLength: 100,
  auto: true,
})
const repOptions = computed(() => (reps.data || []).map((r) => ({ label: r.full_name || r.name, value: r.name })))

// Only the fields a creator sets; sync bookkeeping and coordinates are managed
// by the sync engine and the first check-in respectively.
const FIELDS = [
  'retailer_name',
  'owner_name',
  'phone',
  'email',
  'sales_rep',
  'retailer_type',
  'gstin',
  'status',
  'address_line1',
  'address_line2',
  'city',
  'state',
  'pincode',
  'geofence_radius_m',
  'latitude',
  'longitude',
]
const emptyForm = () => ({
  retailer_name: '',
  owner_name: '',
  phone: '',
  email: '',
  sales_rep: null,
  retailer_type: '',
  gstin: '',
  status: 'Active',
  address_line1: '',
  address_line2: '',
  city: '',
  state: '',
  pincode: '',
  geofence_radius_m: '',
  latitude: '',
  longitude: '',
})
const form = reactive(emptyForm())
const loaded = ref(null)
const busy = ref(false)

function resetForm() {
  Object.assign(form, emptyForm())
  loaded.value = null
}

// The doc being edited (prefill source).
const editDoc = createResource({
  url: 'frappe.client.get',
  makeParams: () => ({ doctype: 'Retailer', name: props.retailer }),
})

watch(open, async (value) => {
  if (!value) return
  if (props.retailer) {
    try {
      const doc = await editDoc.fetch()
      for (const field of FIELDS) {
        form[field] = field === 'sales_rep' ? doc[field] || null : doc[field] ?? ''
      }
      loaded.value = { ...form }
    } catch {
      toast.error('Could not load the retailer')
      open.value = false
    }
  } else {
    // A rep's own retailer defaults to them; the server assigns it anyway on insert.
    form.sales_rep = session.salesRep?.name || null
  }
})

// Matches Retailer._guard_location: reps set a retailer's first location; after that only
// managers move it, and only managers change the geofence radius.
const locationLocked = computed(
  () => isEdit.value && !session.isManager && !!(Number(loaded.value?.latitude) || Number(loaded.value?.longitude)),
)

const valid = computed(() => form.retailer_name.trim() && form.phone.trim() && form.address_line1.trim() && form.city.trim())

// GPS fix for the coordinates fields (same helper and rules as the visit check-in).
const locating = ref(false)
async function useMyLocation() {
  locating.value = true
  try {
    const coords = await getPosition()
    form.latitude = Number(coords.latitude.toFixed(6))
    form.longitude = Number(coords.longitude.toFixed(6))
    toast.success('Location captured')
  } catch (error) {
    toast.error(error.message)
  } finally {
    locating.value = false
  }
}

// Map picker for the same two fields.
const pickerOpen = ref(false)
function applyPickedLocation({ latitude, longitude }) {
  form.latitude = latitude
  form.longitude = longitude
}

async function submit() {
  busy.value = true
  try {
    if (isEdit.value) {
      await saveChanges()
    } else {
      await createRetailer()
    }
  } catch {
    // callMethod already toasted the error (e.g. phone must be 10 digits).
  } finally {
    busy.value = false
  }
}

// Inputs yield strings; the numeric fields go over the wire as numbers.
const NUMBER_FIELDS = new Set(['geofence_radius_m', 'latitude', 'longitude'])
const asPayload = (field, value) =>
  value === '' || value == null ? value : NUMBER_FIELDS.has(field) ? Number(value) : value

async function createRetailer() {
  // Only filled fields: the doctype's own defaults (series, sync status) apply.
  const doc = { doctype: 'Retailer' }
  for (const field of FIELDS) {
    const value = asPayload(field, form[field])
    if (value !== '' && value != null) doc[field] = value
  }
  const inserted = await callMethod('frappe.client.insert', { doc })
  toast.success(`Customer ${inserted.name} created`)
  open.value = false
  router.push({ name: 'Retailer', params: { name: inserted.name } })
}

async function saveChanges() {
  // Only what changed; '' clears a field server-side.
  const fieldname = {}
  for (const field of FIELDS) {
    if (String(form[field] ?? '') !== String(loaded.value?.[field] ?? '')) {
      fieldname[field] = form[field] === null ? '' : asPayload(field, form[field])
    }
  }
  if (!Object.keys(fieldname).length) {
    open.value = false
    return
  }
  const saved = await callMethod('frappe.client.set_value', {
    doctype: 'Retailer',
    name: props.retailer,
    fieldname,
  })
  toast.success(`Customer ${saved.name || props.retailer} updated`)
  open.value = false
  loaded.value = { ...form }
  emit('updated')
}
</script>
