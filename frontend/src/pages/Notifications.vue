<template>
  <div>
    <PageHeader title="Notifications">
      <template #meta>
        <span v-if="unread" class="text-polaris-secondary">{{ unread }} unread</span>
      </template>
      <template #actions>
        <Button label="Mark all as read" icon-left="lucide-check-check" :disabled="!unread" :loading="markingAll" @click="markAll" />
      </template>
    </PageHeader>

    <div class="px-4 sm:px-6 pb-6 sm:pb-8">
      <SectionCard flush>
        <div v-if="list.loading && !rows.length" class="flex justify-center py-16"><LoadingIndicator class="size-5" /></div>
        <EmptyState v-else-if="!rows.length" icon="lucide-bell" title="No notifications" description="Mentions, assignments and alerts for you show up here." />
        <ul v-else class="divide-y divide-[#f1f2f4]">
          <li v-for="row in rows" :key="row.name">
            <button type="button" class="flex w-full items-start gap-3 px-4 py-3.5 text-left transition-colors duration-100 hover:bg-[#f9fafb]" @click="open(row)">
              <span class="mt-1.5 size-2 shrink-0 rounded-full" :class="row.read ? 'bg-transparent' : 'bg-[#005bd3]'" />
              <div class="min-w-0 flex-1">
                <div class="line-clamp-2" :class="row.read ? 'text-polaris-secondary' : 'font-semibold text-polaris-text'">{{ row.subject }}</div>
                <div class="mt-0.5 text-xs text-polaris-subdued">
                  {{ [row.from_user, row.document_type && row.document_name ? `${row.document_type} ${row.document_name}` : null].filter(Boolean).join(' · ') }}
                </div>
              </div>
              <span class="shrink-0 text-xs text-polaris-subdued">{{ dayjs(row.creation).fromNow() }}</span>
            </button>
          </li>
        </ul>
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, LoadingIndicator, createResource, dayjs } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import { callMethod } from '@/utils/api'
import { documentTarget } from '@/utils/nav'

const router = useRouter()

const list = createResource({ url: 'field_sales.frontend_api.get_notifications', auto: true })
const rows = computed(() => list.data || [])
const unread = computed(() => rows.value.filter((r) => !r.read).length)

// Tells the sidebar to refresh its unread count.
const notifyChanged = () => window.dispatchEvent(new Event('fs:notifications-changed'))

async function markRead(row) {
  if (row.read) return
  row.read = 1
  await callMethod('frappe.desk.doctype.notification_log.notification_log.mark_as_read', { docname: row.name })
  notifyChanged()
}

async function open(row) {
  markRead(row).catch(() => (row.read = 0))
  const target = documentTarget(row.document_type, row.document_name)
  if (target?.route) router.push(target.route)
  else if (target?.href) window.open(target.href, '_blank', 'noopener')
}

const markingAll = ref(false)
async function markAll() {
  markingAll.value = true
  try {
    await callMethod('frappe.desk.doctype.notification_log.notification_log.mark_all_as_read')
    rows.value.forEach((r) => (r.read = 1))
    notifyChanged()
  } finally {
    markingAll.value = false
  }
}
</script>
