import { computed, ref, watch } from 'vue'
import { createListResource, createResource } from 'frappe-ui'

/**
 * A paged, filterable list of one doctype, plus its total count.
 * Goes through frappe.client.get_list, so desk permissions (and the Sales Rep
 * hierarchy) apply.
 *
 * A `preset` (key of frontend_api.get_attention_presets) adds the server's
 * filters for that dashboard card, so the list shows exactly what the card
 * counted. The page's own tab/search/filters narrow it further.
 *
 * @param {object} options
 * @param {string} options.doctype
 * @param {string[]} options.fields
 * @param {string} options.orderBy
 * @param {(model: object) => Array} options.buildFilters  ListFilters model -> frappe filters
 * @param {string[]} [options.searchFields]  fields the search box matches (any of them)
 * @param {object} [options.initial]  initial ListFilters model
 * @param {string|null} [options.preset]  dashboard preset key (e.g. from ?preset=)
 */
export function useDocList({
  doctype,
  fields,
  orderBy,
  buildFilters,
  searchFields = [],
  initial = {},
  pageLength = 20,
  preset = null,
}) {
  // Replaced wholesale by ListFilters (v-model), so a ref, not a reactive.
  const model = ref({ search: '', ...initial })

  // Not cached: a cached resource is shared between page visits and keeps the
  // first visit's callbacks, so a second visit's list would never load.
  const presetResource = createResource({
    url: 'field_sales.frontend_api.get_list_preset',
    params: { key: preset },
    auto: !!preset,
  })
  const presetLabel = computed(() => (preset ? presetResource.data?.label || '…' : null))

  const filters = () => [...(preset ? presetResource.data?.filters || [] : []), ...buildFilters(model.value)]
  const orFilters = () =>
    model.value.search ? searchFields.map((field) => [field, 'like', `%${model.value.search}%`]) : []

  // With a preset, wait for its filters: an unfiltered first page would flash the wrong rows.
  const list = createListResource({
    doctype,
    fields,
    orderBy,
    pageLength,
    filters: filters(),
    orFilters: orFilters(),
    auto: !preset,
  })

  const count = createResource({
    url: 'frappe.desk.reportview.get_count',
    makeParams: () => ({ doctype, filters: list.filters, or_filters: list.orFilters }),
    auto: !preset,
  })

  function refresh() {
    if (preset && !presetResource.data) return
    list.filters = filters()
    list.orFilters = orFilters()
    list.start = 0
    list.reload()
    count.reload()
  }

  watch(model, refresh, { deep: true })

  // Load the list once this page's preset filters have arrived.
  watch(
    () => presetResource.data,
    (data) => {
      if (!data) return
      if (data.doctype !== doctype) {
        console.error(`Preset ${preset} is for ${data.doctype}, not ${doctype}`)
        return
      }
      refresh()
    },
  )

  return { model, list, count, refresh, presetLabel }
}
