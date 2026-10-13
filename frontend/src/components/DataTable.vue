<template>
  <div class="fs-card overflow-hidden">
    <div class="overflow-x-auto">
      <table class="w-full text-left">
        <thead>
          <tr class="border-b border-desk-border bg-surface-gray-1 text-ink-gray-5">
            <th
              v-for="col in columns"
              :key="col.key"
              class="whitespace-nowrap px-4 py-2 text-xs font-medium"
              :class="col.align === 'right' && 'text-right'"
              :style="col.width && { width: col.width }"
            >
              {{ col.label }}
            </th>
          </tr>
        </thead>
        <tbody>
          <template v-if="loading && !rows.length">
            <tr v-for="i in 5" :key="'s' + i" class="border-b border-desk-border last:border-0">
              <td v-for="col in columns" :key="col.key" class="px-4 py-3">
                <div class="h-3 w-3/4 animate-pulse rounded-3 bg-surface-gray-2" />
              </td>
            </tr>
          </template>
          <tr
            v-for="row in rows"
            :key="row.name"
            class="border-b border-desk-border last:border-0"
            :class="rowRoute && 'cursor-pointer hover:bg-surface-gray-1'"
            @click="rowRoute && $router.push(rowRoute(row))"
          >
            <td
              v-for="col in columns"
              :key="col.key"
              class="px-4 py-2.5 text-ink-gray-8"
              :class="[col.align === 'right' && 'text-right tabular-nums', col.class]"
            >
              <slot :name="'cell-' + col.key" :row="row" :value="row[col.key]">
                {{ col.format ? col.format(row[col.key], row) : row[col.key] ?? '—' }}
              </slot>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <EmptyState v-if="!loading && !rows.length" :icon="emptyIcon" :title="emptyTitle" :description="emptyDescription" />

    <div
      v-if="rows.length"
      class="flex items-center justify-between border-t border-desk-border px-4 py-2 text-xs text-ink-gray-5"
    >
      <span>{{ rows.length }} of {{ total ?? rows.length }}</span>
      <Button v-if="hasMore" label="Load more" size="sm" :loading="loading" @click="$emit('load-more')" />
    </div>
  </div>
</template>

<script setup>
import { Button } from 'frappe-ui'
import EmptyState from './EmptyState.vue'

defineProps({
  /** [{ key, label, align?: 'right', width?, format?: (value, row) => string, class? }] */
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  total: { type: Number, default: null },
  hasMore: { type: Boolean, default: false },
  /** row => route location; makes rows clickable */
  rowRoute: { type: Function, default: null },
  emptyTitle: { type: String, default: 'Nothing here yet' },
  emptyDescription: { type: String, default: null },
  emptyIcon: { type: String, default: 'lucide-inbox' },
})
defineEmits(['load-more'])
</script>
