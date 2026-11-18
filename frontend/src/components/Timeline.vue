<template>
  <!-- Shopify's timeline (as on an order in the Shopify admin): a comment box at the top,
       then the record's history, newest first, grouped by day on a vertical line —
       comments, app events, field changes and emails. Text only: nothing renders as markup. -->
  <SectionCard title="Timeline">
    <template #actions>
      <Button variant="ghost" size="sm" icon="lucide-refresh-cw" aria-label="Refresh" :loading="timeline.loading" @click="timeline.reload()" />
    </template>

    <form class="mb-5" @submit.prevent="post">
      <div class="flex items-start gap-3 rounded-lg border border-[#e3e3e3] bg-white p-2 focus-within:border-polaris-text">
        <Avatar :label="session.fullName" :image="session.userImage" size="md" class="mt-1 shrink-0" />
        <label :for="inputId" class="sr-only">Leave a comment</label>
        <textarea
          :id="inputId"
          v-model="note"
          rows="1"
          maxlength="5000"
          class="min-h-9 flex-1 resize-y border-0 bg-transparent p-1.5 text-sm text-polaris-text placeholder:text-ink-gray-5 focus:ring-0"
          placeholder="Leave a comment…"
          @keydown.ctrl.enter.prevent="post"
          @keydown.meta.enter.prevent="post"
        />
        <Button type="submit" label="Post" :loading="posting" :disabled="!note.trim()" class="mt-1" />
      </div>
      <p class="mt-1.5 text-right text-xs text-ink-gray-5">Only you and other staff can see comments</p>
    </form>

    <p v-if="timeline.error" class="text-sm text-ink-red-6">{{ errorText }}</p>
    <div v-else-if="timeline.loading && !items.length" class="space-y-3">
      <div v-for="i in 3" :key="i" class="h-8 animate-pulse rounded bg-surface-gray-2" />
    </div>
    <p v-else-if="!items.length" class="text-sm text-ink-gray-5">Nothing has happened yet.</p>

    <div v-for="group in groups" :key="group.key" class="mb-2">
      <h3 class="mb-2 text-xs font-semibold text-ink-gray-6">{{ group.day }}</h3>
      <ol class="relative ml-1.5 border-l-2 border-[#e3e3e3]">
        <li v-for="(item, i) in group.items" :key="`${item.at}-${i}`" class="relative pb-4 pl-5 last:pb-1">
          <span
            class="absolute -left-[7px] top-1.5 size-3 rounded-full border-2 border-white"
            :class="item.kind === 'note' ? 'bg-polaris-text' : 'bg-[#b5b5b5]'"
            aria-hidden="true"
          />
          <div class="flex items-start justify-between gap-3 text-sm">
            <div class="min-w-0 flex-1">
              <template v-if="item.kind === 'note'">
                <div class="text-xs font-semibold text-polaris-text">{{ item.by_name }}</div>
                <p class="mt-1 whitespace-pre-line break-words rounded-lg bg-[#f1f1f1] px-3 py-2 text-polaris-text">{{ item.text }}</p>
              </template>
              <p v-else-if="item.kind === 'info'" class="whitespace-pre-line break-words text-polaris-text">{{ item.text }}</p>
              <template v-else-if="item.kind === 'email'">
                <p class="text-polaris-text">{{ item.text }}: <span class="font-medium">{{ item.subject }}</span></p>
              </template>
              <template v-else-if="item.kind === 'change'">
                <p class="text-polaris-text">{{ item.by_name }} changed</p>
                <ul class="mt-0.5 space-y-0.5 text-ink-gray-6">
                  <li v-for="change in item.changes" :key="change.label" class="break-words">
                    {{ change.label }}: {{ change.from || 'blank' }} → <span class="text-polaris-text">{{ change.to || 'blank' }}</span>
                  </li>
                </ul>
              </template>
            </div>
            <time class="shrink-0 text-xs text-ink-gray-5" :datetime="item.at" :title="formatDateTime(item.at)">{{ formatTime(item.at) }}</time>
          </div>
        </li>
      </ol>
    </div>
  </SectionCard>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Avatar, Button, createResource, toast } from 'frappe-ui'
import SectionCard from './SectionCard.vue'
import { callMethod } from '@/utils/api'
import { formatDate, formatDateTime, formatTime, relativeDay } from '@/utils/format'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  doctype: { type: String, required: true },
  name: { type: String, required: true },
  /** The record's `modified` (or status): the timeline reloads when the record changes. */
  version: { type: String, default: null },
})

const session = useSessionStore()
const timeline = createResource({
  url: 'field_sales.chatter.get_chatter',
  // get_chatter accepts GET only; frappe-ui resources send POST unless told otherwise.
  method: 'GET',
  makeParams: () => ({ doctype: props.doctype, name: props.name }),
  auto: true,
})
watch(
  () => props.version,
  (now, before) => before && now !== before && timeline.reload(),
)

const items = computed(() => timeline.data?.items || [])
const errorText = computed(() => String(timeline.error?.messages?.[0] || timeline.error || 'Could not load the timeline'))

const groups = computed(() => {
  const out = []
  for (const item of items.value) {
    const key = String(item.at).slice(0, 10)
    const day = ['Today', 'Yesterday'].includes(relativeDay(key)) ? relativeDay(key) : formatDate(key)
    if (!out.length || out[out.length - 1].key !== key) out.push({ key, day, items: [] })
    out[out.length - 1].items.push(item)
  }
  return out
})

const inputId = `timeline-note-${Math.random().toString(36).slice(2, 7)}`
const note = ref('')
const posting = ref(false)

async function post() {
  const text = note.value.trim()
  if (!text || posting.value) return
  posting.value = true
  try {
    await callMethod('field_sales.chatter.post_note', { doctype: props.doctype, name: props.name, text })
    note.value = ''
    toast.success('Comment posted')
    timeline.reload()
  } catch {
    // callMethod shows the server's message; the comment stays in the box.
  } finally {
    posting.value = false
  }
}
</script>
