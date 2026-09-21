<template>
  <div id="app" class="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 dark:from-slate-900 dark:to-slate-800">
    <!-- Mobile Header -->
    <header class="sticky top-0 z-40 bg-white dark:bg-slate-800 shadow-sm border-b border-slate-200 dark:border-slate-700">
      <div class="px-4 py-3 sm:px-6">
        <div class="flex items-center justify-between">
          <h1 class="text-xl font-bold text-slate-900 dark:text-white">Delta Basket</h1>
          <div class="flex items-center gap-3">
            <!-- Status indicator -->
            <div class="flex items-center gap-2" v-if="systemStatus">
              <div
                class="w-2 h-2 rounded-full"
                :class="systemStatus === 'operational' ? 'bg-green-500' : 'bg-yellow-500'"
              ></div>
              <span class="text-xs font-medium text-slate-600 dark:text-slate-400">
                {{ systemStatus }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Maintenance banner -->
      <div v-if="maintenanceMode" class="px-4 py-2 bg-yellow-50 dark:bg-yellow-900/20 border-t border-yellow-200 dark:border-yellow-800">
        <p class="text-xs text-yellow-800 dark:text-yellow-300 font-medium">
          ⚠️ Exchange Maintenance — Monitoring Paused
        </p>
      </div>
    </header>

    <!-- Main Content -->
    <main class="px-4 py-4 sm:px-6 pb-20">
      <div v-if="loading" class="text-center py-12">
        <div class="inline-block">
          <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
        <p class="mt-2 text-sm text-slate-600 dark:text-slate-400">Loading baskets...</p>
      </div>

      <div v-else-if="baskets.length === 0" class="text-center py-12">
        <p class="text-slate-600 dark:text-slate-400 text-sm">No active baskets</p>
        <button
          @click="showCreateModal = true"
          class="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
        >
          Create Basket
        </button>
      </div>

      <div v-else class="space-y-4">
        <!-- Basket Cards -->
        <div
          v-for="basket in baskets"
          :key="basket.id"
          class="bg-white dark:bg-slate-800 rounded-lg shadow border border-slate-200 dark:border-slate-700 overflow-hidden"
        >
          <!-- Basket Header -->
          <div class="bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 px-4 py-3 border-b border-slate-200 dark:border-slate-700">
            <div class="flex items-start justify-between gap-2">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <h2 class="font-bold text-slate-900 dark:text-white text-base">
                    {{ basket.underlying }} {{ basket.expiry_date }}
                  </h2>
                  <span class="inline-block px-2 py-1 rounded text-xs font-semibold"
                    :class="getStateColor(basket.state)"
                  >
                    {{ basket.state }}
                  </span>
                </div>
                <p class="text-xs text-slate-600 dark:text-slate-400 mt-1">
                  ID: {{ basket.id.slice(0, 8) }}...
                </p>
              </div>

              <!-- P&L Display -->
              <div class="text-right">
                <div class="font-bold text-lg"
                  :class="getTotalPnL(basket) >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'"
                >
                  {{ formatPnL(getTotalPnL(basket)) }}
                </div>
                <p class="text-xs text-slate-600 dark:text-slate-400">Total P&L</p>
              </div>
            </div>
          </div>

          <!-- P&L Breakdown -->
          <div class="px-4 py-3 bg-slate-50 dark:bg-slate-700/30 border-b border-slate-200 dark:border-slate-700">
            <div class="grid grid-cols-2 gap-4">
              <div>
                <p class="text-xs text-slate-600 dark:text-slate-400">Realized</p>
                <p class="font-semibold text-slate-900 dark:text-white">
                  {{ formatPnL(basket.realized_pnl) }}
                </p>
              </div>
              <div>
                <p class="text-xs text-slate-600 dark:text-slate-400">Unrealized</p>
                <p class="font-semibold text-slate-900 dark:text-white">
                  {{ formatPnL(basket.unrealized_pnl) }}
                </p>
              </div>
            </div>
          </div>

          <!-- Legs -->
          <div class="px-4 py-3 space-y-2">
            <p class="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase">Legs</p>
            <div
              v-for="leg in basket.legs"
              :key="leg.id"
              class="flex items-center justify-between p-2 bg-slate-50 dark:bg-slate-700/30 rounded text-sm"
            >
              <div class="flex-1 min-w-0">
                <div class="font-medium text-slate-900 dark:text-white truncate">
                  {{ leg.symbol }}
                </div>
                <div class="text-xs text-slate-600 dark:text-slate-400">
                  {{ leg.side }} • {{ leg.status }}
                </div>
              </div>
              <div class="text-right whitespace-nowrap ml-2">
                <div class="font-semibold text-slate-900 dark:text-white text-sm">
                  {{ formatPnL(leg.unrealized_pnl) }}
                </div>
                <div class="text-xs text-slate-600 dark:text-slate-400">
                  @ {{ leg.current_price ? `$${leg.current_price.toFixed(2)}` : '-' }}
                </div>
              </div>
            </div>
          </div>

          <!-- Actions -->
          <div class="px-4 py-3 bg-slate-50 dark:bg-slate-700/30 border-t border-slate-200 dark:border-slate-700 flex gap-2">
            <button
              @click="refreshBasket(basket.id)"
              class="flex-1 px-3 py-2 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded font-medium text-sm hover:bg-blue-200 dark:hover:bg-blue-900/50"
            >
              Refresh
            </button>
            <button
              @click="closeBasket(basket.id)"
              :disabled="maintenanceMode"
              class="flex-1 px-3 py-2 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded font-medium text-sm hover:bg-red-200 dark:hover:bg-red-900/50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </main>

    <!-- Floating Action Button -->
    <div class="fixed bottom-6 right-6">
      <button
        @click="showCreateModal = true"
        class="w-14 h-14 rounded-full bg-blue-600 dark:bg-blue-700 text-white shadow-lg flex items-center justify-center text-2xl hover:bg-blue-700 dark:hover:bg-blue-800 active:scale-95 transition-transform"
      >
        +
      </button>
    </div>

    <!-- Create Basket Modal -->
    <div v-if="showCreateModal" class="fixed inset-0 bg-black/50 z-50 flex items-end sm:items-center justify-center">
      <div class="bg-white dark:bg-slate-800 w-full sm:max-w-md rounded-t-lg sm:rounded-lg shadow-lg p-6 max-h-96 overflow-y-auto">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-xl font-bold text-slate-900 dark:text-white">Create Basket</h2>
          <button
            @click="showCreateModal = false"
            class="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
          >
            ✕
          </button>
        </div>

        <form @submit.prevent="createBasket" class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
              Underlying
            </label>
            <select
              v-model="newBasket.underlying"
              class="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
              required
            >
              <option value="">Select underlying</option>
              <option value="BTC">Bitcoin (BTC)</option>
              <option value="ETH">Ethereum (ETH)</option>
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
              Expiry Date
            </label>
            <input
              v-model="newBasket.expiry_date"
              type="date"
              class="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
              required
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
              Lot Size
            </label>
            <input
              v-model.number="newBasket.lot_size"
              type="number"
              min="1"
              placeholder="1"
              class="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
              required
            />
          </div>

          <div class="flex gap-3">
            <button
              type="button"
              @click="showCreateModal = false"
              class="flex-1 px-4 py-2 border border-slate-300 dark:border-slate-600 rounded-lg text-slate-700 dark:text-slate-300 font-medium hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              :disabled="!newBasket.underlying || !newBasket.expiry_date || !newBasket.lot_size"
              class="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Create
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted } from 'vue'

export default {
  name: 'App',
  setup() {
    const baskets = ref([])
    const loading = ref(false)
    const showCreateModal = ref(false)
    const systemStatus = ref('operational')
    const maintenanceMode = ref(false)
    const newBasket = ref({ underlying: '', expiry_date: '', lot_size: 1 })
    let refreshInterval

    const createBasket = async () => {
      try {
        const response = await fetch('/baskets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            underlying: newBasket.value.underlying,
            expiry_date: newBasket.value.expiry_date,
            lot_size: parseInt(newBasket.value.lot_size),
            entry_timeout_minutes: 15
          })
        })
        if (response.ok) {
          showCreateModal.value = false
          newBasket.value = { underlying: '', expiry_date: '', lot_size: 1 }
          await fetchBaskets()
          alert('Basket created successfully!')
        } else {
          const error = await response.json()
          alert(`Failed: ${error.detail || JSON.stringify(error)}`)
        }
      } catch (error) {
        console.error('Failed to create basket:', error)
        alert('Failed to create basket: ' + error.message)
      }
    }

    const fetchBaskets = async () => {
      try {
        loading.value = true
        const response = await fetch('/baskets')
        const data = await response.json()
        baskets.value = data.baskets || []
      } catch (error) {
        console.error('Failed to fetch baskets:', error)
      } finally {
        loading.value = false
      }
    }

    const fetchHealth = async () => {
      try {
        const response = await fetch('/health')
        const data = await response.json()
        systemStatus.value = data.delta_status
        maintenanceMode.value = data.delta_status === 'maintenance'
      } catch (error) {
        console.error('Failed to fetch health:', error)
      }
    }

    const refreshBasket = async (basketId) => {
      await fetchBaskets()
    }

    const closeBasket = async (basketId) => {
      if (confirm('Close this basket?')) {
        try {
          const response = await fetch(`/baskets/${basketId}/close`, {
            method: 'POST'
          })
          if (response.ok) {
            await fetchBaskets()
          }
        } catch (error) {
          console.error('Failed to close basket:', error)
          alert('Failed to close basket')
        }
      }
    }

    const getTotalPnL = (basket) => {
      return (basket.realized_pnl || 0) + (basket.unrealized_pnl || 0)
    }

    const formatPnL = (value) => {
      const num = parseFloat(value)
      if (isNaN(num)) return '$0.00'
      return `$${num.toFixed(2)}`
    }

    const getStateColor = (state) => {
      const colors = {
        building: 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-300',
        active: 'bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300',
        closing: 'bg-orange-100 dark:bg-orange-900/30 text-orange-800 dark:text-orange-300',
        closed: 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-300',
      }
      return colors[state] || colors.building
    }

    onMounted(() => {
      fetchBaskets()
      fetchHealth()
      refreshInterval = setInterval(() => {
        fetchBaskets()
        fetchHealth()
      }, 2000) // Refresh every 2 seconds
    })

    onUnmounted(() => {
      if (refreshInterval) {
        clearInterval(refreshInterval)
      }
    })

    return {
      baskets,
      loading,
      showCreateModal,
      systemStatus,
      maintenanceMode,
      newBasket,
      fetchBaskets,
      createBasket,
      refreshBasket,
      closeBasket,
      getTotalPnL,
      formatPnL,
      getStateColor,
    }
  }
}
</script>

<style scoped>
/* Smooth transitions */
.transition-all {
  transition: all 150ms ease-in-out;
}

/* Mobile optimizations */
@media (max-width: 640px) {
  /* Increase tap target sizes */
  button {
    min-height: 44px;
  }
}
</style>
