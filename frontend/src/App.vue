<template>
  <FrappeUIProvider>
    <AppLayout v-if="session.ready && session.user">
      <RouterView :key="$route.fullPath" />
    </AppLayout>
    <div v-else-if="session.error" class="flex h-full items-center justify-center p-6">
      <div class="fs-card w-full max-w-md">
        <EmptyState
          :icon="session.error.noAccess ? 'lucide-lock' : 'lucide-circle-alert'"
          :title="session.error.title"
          :description="session.error.message"
        >
          <div class="flex gap-2">
            <Button v-if="!session.error.noAccess" label="Retry" variant="solid" @click="retry" />
            <Button label="Go to Desk" href="/app" />
          </div>
        </EmptyState>
      </div>
    </div>
    <div v-else class="flex h-full items-center justify-center text-ink-gray-5">
      <LoadingIndicator class="size-5" />
    </div>
  </FrappeUIProvider>
</template>

<script setup>
import { Button, FrappeUIProvider, LoadingIndicator } from 'frappe-ui'
import { useRouter } from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const router = useRouter()

async function retry() {
  if (await session.retry()) router.replace(router.currentRoute.value.fullPath)
}
</script>
