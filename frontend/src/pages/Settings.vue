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
        <template v-if="canEdit && activeForm?.isDirty">
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
          v-if="session.isAdmin && tab === 'store' && !embedded"
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
      <!-- Sales Managers may also edit Razorpay; the other tabs are System Manager only -->
      <RazorpaySettings v-if="tab === 'razorpay' && canEdit" ref="razorpayForm" />
      <!-- Inside the Shopify admin the install owns the connection: a read-only summary for managers -->
      <ShopifyStoreStatus v-else-if="embedded && tab === 'store' && session.isManager" />
      <template v-else-if="session.isAdmin">
        <!-- v-show keeps unsaved Store Connection edits while another tab is open -->
        <StoreConnectionForm v-if="!embedded" v-show="tab === 'store'" ref="storeForm" />
        <InvoiceSettings v-if="tab === 'invoice'" ref="invoiceForm" @saved="refreshStoreForm" />
        <CommissionSettings v-if="tab === 'commission'" ref="commissionForm" @saved="refreshStoreForm" />
        <EmailAccountSettings v-if="tab === 'email'" />
      </template>
      <div v-else class="fs-card">
        <EmptyState
          :icon="NOT_ADMIN[tab].icon"
          :title="NOT_ADMIN[tab].title"
          :description="`Only a ${NOT_ADMIN[tab].who || 'System Manager'} can change ${NOT_ADMIN[tab].what}.`"
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
import CommissionSettings from '@/components/CommissionSettings.vue'
import EmailAccountSettings from '@/components/EmailAccountSettings.vue'
import RazorpaySettings from '@/components/RazorpaySettings.vue'
import { useSessionStore } from '@/stores/session'
import ShopifyStoreStatus from '@/shopify/ShopifyStoreStatus.vue'
import { isEmbedded } from '@/shopify/embedded'

const session = useSessionStore()
const embedded = isEmbedded()
const storeForm = ref(null)
const invoiceForm = ref(null)
const commissionForm = ref(null)
const razorpayForm = ref(null)

const TABS = [
  { label: 'Store Connection', value: 'store' },
  { label: 'Invoice', value: 'invoice' },
  { label: 'Commission', value: 'commission' },
  { label: 'Email Account', value: 'email' },
  { label: 'Razorpay', value: 'razorpay' },
]
const NOT_ADMIN = {
  store: { icon: 'lucide-store', title: 'Store Connection', what: 'the store connection' },
  invoice: { icon: 'lucide-file-text', title: 'Invoice', what: 'the invoice settings' },
  commission: { icon: 'lucide-wallet', title: 'Commission', what: 'the commission settings' },
  email: { icon: 'lucide-mail', title: 'Email Account', what: 'the email account' },
  razorpay: { icon: 'lucide-credit-card', title: 'Razorpay', what: 'the Razorpay settings', who: 'Sales Manager or System Manager' },
}
// The form whose Save / Discard the header shows (the Email tab saves in its own dialog).
const FORMS = { store: storeForm, invoice: invoiceForm, commission: commissionForm, razorpay: razorpayForm }
const activeForm = computed(() => FORMS[tab.value]?.value || null)
const canEdit = computed(() => (tab.value === 'razorpay' ? session.isManager : session.isAdmin))

// These forms all edit the Store Connection document: after an Invoice or Commission save, the Store form
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

