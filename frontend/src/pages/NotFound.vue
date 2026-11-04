<template>
  <div class="px-4 sm:px-6 pt-6">
    <div class="fs-card">
      <EmptyState icon="lucide-compass" title="Page not found" description="The page you are looking for does not exist or may have moved.">
        <Button variant="solid" label="Go to Dashboard" :route="{ name: 'Dashboard' }" />
      </EmptyState>
    </div>
  </div>
</template>

<script setup>
import { Button } from 'frappe-ui'
import EmptyState from '@/components/EmptyState.vue'
</script>
