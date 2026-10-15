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
        <template v-if="session.isAdmin && tab === 'store' && storeForm?.isDirty">
          <Button
            variant="ghost"
            label="Discard"
            :disabled="storeForm.saving || storeForm.testing"
            @click="storeForm.discard()"
          />
          <Button
            variant="solid"
            label="Save Changes"
            :loading="storeForm.saving"
            :disabled="storeForm.testing"
            @click="storeForm.save()"
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
        <!-- v-show keeps unsaved Store Connection edits while the Email tab is open -->
        <StoreConnectionForm v-show="tab === 'store'" ref="storeForm" />
        <EmailAccountSettings v-if="tab === 'email'" />
      </template>
      <div v-else class="fs-card">
        <EmptyState
          :icon="tab === 'email' ? 'lucide-mail' : 'lucide-store'"
          :title="tab === 'email' ? 'Email Account' : 'Store Connection'"
          :description="`Only a System Manager can change the ${tab === 'email' ? 'email account' : 'store connection'}.`"
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
import EmailAccountSettings from '@/components/EmailAccountSettings.vue'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const storeForm = ref(null)

const TABS = [
  { label: 'Store Connection', value: 'store' },
  { label: 'Email Account', value: 'email' },
]
// The tab is in the URL (?tab=email), so a refresh or a link opens it.
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => (route.query.tab === 'email' ? 'email' : 'store'),
  set: (value) => router.replace({ query: value === 'store' ? {} : { tab: value } }),
})
</script>

