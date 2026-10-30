<template>
  <div>
    <PageHeader title="Reports" />
    <div class="px-4 sm:px-6 pb-6 sm:pb-8">
      <SectionCard flush>
        <ul v-if="reports.length" class="divide-y divide-[#f1f2f4]">
          <li v-for="report in reports" :key="report.label">
            <RouterLink
              :to="report.route"
              class="flex items-center gap-3 px-4 sm:px-5 py-3.5 transition-colors duration-100 hover:bg-[#f9fafb] focus:outline-none"
            >
              <span :class="report.icon" class="size-4 shrink-0 text-polaris-secondary" aria-hidden="true" />
              <span class="min-w-0 flex-1">
                <span class="block font-medium text-polaris-text">{{ report.label }}</span>
                <span class="block truncate text-xs text-polaris-secondary">{{ report.description }}</span>
              </span>
              <span class="lucide-chevron-right size-4 shrink-0 text-polaris-subdued" aria-hidden="true" />
            </RouterLink>
          </li>
        </ul>
        <EmptyState v-else icon="lucide-chart-column" title="No reports" description="Reports you can open appear here." />
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useSessionStore } from '@/stores/session'
import { REPORT_LINKS } from '@/utils/nav'

const session = useSessionStore()

const reports = computed(() => REPORT_LINKS.filter((r) => session.canSee(r)))
</script>
