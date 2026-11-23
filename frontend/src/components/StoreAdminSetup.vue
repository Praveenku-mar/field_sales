<template>
  <!-- Right after a Shopify install (shared site): the merchant's admin email, before the app. -->
  <div class="fs-card w-full max-w-md p-6">
    <template v-if="!sentTo">
      <h1 class="text-base font-semibold text-polaris-text">Who manages this store?</h1>
      <p class="mt-1 text-sm text-polaris-secondary">
        Enter the admin's email. We send an invitation to set a password, so the admin can also sign in on the web
        and in the mobile app.
      </p>
      <form class="mt-5 flex flex-col gap-4" @submit.prevent="submit">
        <TextInput v-model="email" type="email" label="Admin email" placeholder="owner@yourstore.com" required />
        <Button type="submit" variant="solid" label="Send invitation" :loading="sending" :disabled="!email.trim()" />
      </form>
    </template>
    <template v-else>
      <h1 class="text-base font-semibold text-polaris-text">Invitation sent</h1>
      <p class="mt-1 text-sm text-polaris-secondary">
        We sent an invitation to <b class="text-polaris-text">{{ sentTo }}</b>. Open it to set your password; then
        sign in with this email on the web and in the mobile app. Inside Shopify you are signed in as this account.
      </p>
      <Button class="mt-5 w-full" variant="solid" label="Continue" @click="reload" />
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Button, TextInput } from 'frappe-ui'
import { callMethod } from '@/utils/api'

const email = ref('')
const sending = ref(false)
const sentTo = ref('')

async function submit() {
  sending.value = true
  try {
    const result = await callMethod('field_sales.shopify.store_admin.set_store_admin', { email: email.value.trim() })
    sentTo.value = result?.email || email.value.trim()
  } catch {
    // callMethod showed the error
  } finally {
    sending.value = false
  }
}

function reload() {
  // The next request signs in as the new admin account (the store's Shopify sign-in now).
  window.location.reload()
}
</script>
