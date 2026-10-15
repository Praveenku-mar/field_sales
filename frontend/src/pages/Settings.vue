<template>
  <div>
    <PageHeader title="Settings">
      <template #meta>
        <TabButtons v-model="tab" :options="TABS" />
        <StatusPill
          v-if="tab === 'store' && storeForm?.titleStatus"
          :status="storeForm.titleStatus.status"
          :label="storeForm.titleStatus.label"
          :icon="storeForm.titleStatus.icon"
        />
      </template>
      <template #actions>
        <template v-if="session.isAdmin && activeForm?.isDirty">
          <Button
            variant="ghost"
            label="Discard"
            :disabled="activeForm.saving || activeForm.testing"
            @click="activeForm.discard()"
          />
          <Button
            variant="solid"
            label="Save Changes"
            :loading="activeForm.saving"
            :disabled="activeForm.testing"
            @click="activeForm.save()"
          />
        </template>
        <Button
          v-if="session.isAdmin && tab === 'store'"
          :variant="storeForm?.isDirty ? 'subtle' : 'solid'"
          label="Fetch Locations"
          icon-left="lucide-refresh-cw"
          :loading="!!storeForm?.fetchingLocations"
          :disabled="!storeForm"
          @click="storeForm?.fetchLocations()"
        />
      </template>
    </PageHeader>
    <div class="px-5 pb-5">
      <template v-if="session.isAdmin">
        <!-- v-show keeps unsaved Store Connection edits while another tab is open -->
        <StoreConnectionForm v-show="tab === 'store'" ref="storeForm" />
        <InvoiceSettings v-if="tab === 'invoice'" ref="invoiceForm" @saved="refreshStoreForm" />
        <EmailAccountSettings v-if="tab === 'email'" />
      </template>
      <div v-else class="fs-card">
        <EmptyState
          :icon="NOT_ADMIN[tab].icon"
          :title="NOT_ADMIN[tab].title"
          :description="`Only a System Manager can change ${NOT_ADMIN[tab].what}.`"
        >
        </EmptyState>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, TabButtons } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusPill from '@/components/StatusPill.vue'
import StoreConnectionForm from '@/components/StoreConnectionForm.vue'
import InvoiceSettings from '@/components/InvoiceSettings.vue'
import EmailAccountSettings from '@/components/EmailAccountSettings.vue'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const storeForm = ref(null)
const invoiceForm = ref(null)

const TABS = [
  { label: 'Store Connection', value: 'store' },
  { label: 'Invoice', value: 'invoice' },
  { label: 'Email Account', value: 'email' },
]
const NOT_ADMIN = {
  store: { icon: 'lucide-store', title: 'Store Connection', what: 'the store connection' },
  invoice: { icon: 'lucide-file-text', title: 'Invoice', what: 'the invoice settings' },
  email: { icon: 'lucide-mail', title: 'Email Account', what: 'the email account' },
}
// The form whose Save / Discard the header shows (the Email tab saves in its own dialog).
const activeForm = computed(() => (tab.value === 'store' ? storeForm.value : tab.value === 'invoice' ? invoiceForm.value : null))

// Both forms edit the Store Connection document: after an Invoice save, the Store form
// reloads so its next save is not refused as out of date (unless it holds unsaved edits).
function refreshStoreForm() {
  if (storeForm.value && !storeForm.value.isDirty) storeForm.value.discard()
}

// The tab is in the URL (?tab=email), so a refresh or a link opens it.
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => (route.query.tab in NOT_ADMIN ? route.query.tab : 'store'),
  set: (value) => router.replace({ query: value === 'store' ? {} : { tab: value } }),
})
</script>

