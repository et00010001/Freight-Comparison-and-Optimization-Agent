<template>
  <div class="card">
    <!-- 左侧：数据概览（可点击） -->
    <div class="card-title" @click="previewVisible = true">
      <span class="title-clickable">数据概览</span>
      <small>当前费率库 · 点击浏览</small>
      <span v-if="statistics.has_service_rating !== undefined" class="rating-badge">
        {{ statistics.has_service_rating ? '⭐ 含服务评分' : '📊 无服务评分' }}
      </span>
    </div>

    <!-- 中间：统计数字 -->
    <div class="stats-grid">
      <div class="stat-item">
        <div class="number">{{ statistics.total_records || 0 }}</div>
        <div class="label">报价记录</div>
      </div>
      <div class="stat-item">
        <div class="number">{{ statistics.carriers?.length || 0 }}</div>
        <div class="label">承运商</div>
      </div>
      <div class="stat-item">
        <div class="number">{{ statistics.orig_ports?.length || 0 }}</div>
        <div class="label">起运港</div>
      </div>
      <div class="stat-item">
        <div class="number">{{ statistics.transport_modes?.length || 0 }}</div>
        <div class="label">运输方式</div>
      </div>
    </div>

    <!-- 右侧：拖拽上传 -->
    <div
      class="upload-area"
      :class="{ 'upload-dragover': isDragover, 'upload-success': uploadResult?.success }"
      @dragover.prevent="isDragover = true"
      @dragleave.prevent="isDragover = false"
      @drop.prevent="handleDrop"
      @click="triggerFileInput"
    >
      <input
        ref="fileInput"
        type="file"
        accept=".csv,.xlsx,.xls"
        style="display:none"
        @change="handleFileSelect"
      />
      <div v-if="uploading" class="upload-status">
        <span class="upload-icon">⏳</span>
        <span>上传中...</span>
      </div>
      <div v-else-if="uploadResult?.success" class="upload-status">
        <span class="upload-icon">✅</span>
        <span>{{ uploadResult.total_records }} 条记录已导入</span>
      </div>
      <div v-else class="upload-status">
        <span class="upload-icon">📁</span>
        <span>拖入 CSV/Excel<br/>更新数据</span>
      </div>
    </div>

    <!-- 数据浏览弹窗 -->
    <el-dialog
      v-model="previewVisible"
      title="数据浏览"
      width="90%"
      top="5vh"
      :close-on-click-modal="true"
    >
      <div class="preview-info">
        <span>共 {{ previewTotal }} 条记录 · 第 {{ previewPage }}/{{ previewTotalPages }} 页</span>
      </div>
      <el-table
        :data="previewRows"
        border
        stripe
        size="small"
        max-height="500"
        style="width: 100%"
      >
        <el-table-column prop="carrier" label="承运商" width="90" fixed />
        <el-table-column prop="orig_port" label="起运港" width="80" />
        <el-table-column prop="dest_port" label="目的港" width="80" />
        <el-table-column prop="min_weight" label="最小重量" width="90" />
        <el-table-column prop="max_weight" label="最大重量" width="90" />
        <el-table-column prop="service_level" label="服务级别" width="80" />
        <el-table-column prop="min_cost" label="最低费用" width="90">
          <template #default="{ row }">${{ row.min_cost?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="rate" label="费率" width="80">
          <template #default="{ row }">{{ row.rate?.toFixed(4) }}</template>
        </el-table-column>
        <el-table-column prop="mode" label="运输方式" width="80" />
        <el-table-column prop="transport_days" label="运输天数" width="80" />
        <el-table-column prop="carrier_type" label="承运商类型" width="110" />
        <el-table-column prop="service_rating" label="服务评级" width="80">
          <template #default="{ row }">
            <el-tag :type="ratingTagType(row.service_rating)" size="small">
              {{ row.service_rating || '-' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div class="preview-pagination">
        <el-pagination
          v-model:current-page="previewPage"
          v-model:page-size="previewPageSize"
          :total="previewTotal"
          :page-sizes="[20, 50, 100, 200]"
          layout="sizes, prev, pager, next"
          @current-change="loadPreview"
          @size-change="handleSizeChange"
        />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  statistics: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['data-uploaded'])

// ---- 上传 ----
const fileInput = ref(null)
const isDragover = ref(false)
const uploading = ref(false)
const uploadResult = ref(null)

function triggerFileInput() {
  fileInput.value?.click()
}

function handleDrop(e) {
  isDragover.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file) uploadFile(file)
}

function handleFileSelect(e) {
  const file = e.target.files?.[0]
  if (file) uploadFile(file)
  e.target.value = ''
}

async function uploadFile(file) {
  const name = file.name.toLowerCase()
  if (!name.endsWith('.csv') && !name.endsWith('.xlsx') && !name.endsWith('.xls')) {
    ElMessage.error('仅支持 CSV 和 Excel 文件')
    return
  }

  uploading.value = true
  uploadResult.value = null

  try {
    const formData = new FormData()
    formData.append('file', file)

    const res = await fetch('/api/upload_data', { method: 'POST', body: formData })
    const data = await res.json()

    if (!res.ok) {
      ElMessage.error(data.detail || '上传失败')
      return
    }

    uploadResult.value = data
    ElMessage.success(data.message)
    emit('data-uploaded', data)
  } catch (e) {
    ElMessage.error('上传失败: ' + e.message)
  } finally {
    uploading.value = false
  }
}

// ---- 数据浏览 ----
const previewVisible = ref(false)
const previewRows = ref([])
const previewTotal = ref(0)
const previewPage = ref(1)
const previewPageSize = ref(50)
const previewTotalPages = computed(() => Math.ceil(previewTotal.value / previewPageSize.value))

async function loadPreview(page) {
  try {
    const res = await fetch(`/api/data_preview?page=${page || 1}&page_size=${previewPageSize.value}`)
    const data = await res.json()
    previewRows.value = data.rows || []
    previewTotal.value = data.total || 0
    previewPage.value = data.page || 1
  } catch {
    previewRows.value = []
  }
}

function handleSizeChange() {
  previewPage.value = 1
  loadPreview(1)
}

// 打开弹窗时加载第一页
import { watch } from 'vue'
watch(previewVisible, (val) => {
  if (val) loadPreview(1)
})

function ratingTagType(rating) {
  if (rating === 'A') return 'success'
  if (rating === 'B') return 'primary'
  if (rating === 'C') return 'info'
  if (rating === 'D') return 'warning'
  if (rating === 'E') return 'danger'
  return 'info'
}
</script>

<style scoped>
.card {
  background: white;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.05);
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr) 140px;
  gap: 16px;
  padding: 16px;
}

.card-title {
  align-items: flex-start;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding-right: 16px;
  cursor: pointer;
  transition: background 0.15s;
  border-radius: 6px;
  margin: -8px;
  padding: 8px 16px 8px 8px;
}

.card-title:hover {
  background: #f0fdfa;
}

.title-clickable {
  color: #1f2937;
  font-size: 16px;
  font-weight: 700;
  text-decoration: underline;
  text-decoration-color: transparent;
  transition: text-decoration-color 0.15s;
}

.card-title:hover .title-clickable {
  text-decoration-color: #0f766e;
}

.card-title small {
  color: #64748b;
  font-size: 12px;
  margin-top: 4px;
}

.rating-badge {
  font-size: 11px;
  margin-top: 6px;
  padding: 2px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: #475569;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
}

.stat-item {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 12px;
}

.stat-item .number {
  color: #0f766e;
  font-size: 22px;
  font-weight: bold;
}

.stat-item .label {
  font-size: 12px;
  color: #64748b;
  margin-top: 5px;
}

/* 上传区域 */
.upload-area {
  border: 2px dashed #d1d5db;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
  padding: 12px;
  min-height: 80px;
}

.upload-area:hover,
.upload-area.upload-dragover {
  border-color: #0f766e;
  background: #f0fdfa;
}

.upload-area.upload-success {
  border-color: #22c55e;
  background: #f0fdf4;
}

.upload-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #64748b;
  text-align: center;
  line-height: 1.4;
}

.upload-icon {
  font-size: 20px;
}

/* 数据浏览弹窗 */
.preview-info {
  margin-bottom: 12px;
  font-size: 13px;
  color: #64748b;
}

.preview-pagination {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}

@media (max-width: 720px) {
  .card {
    grid-template-columns: 1fr;
  }

  .card-title {
    border-right: 0;
    border-bottom: 1px solid #e2e8f0;
    padding: 0 0 12px;
  }

  .upload-area {
    min-height: 60px;
  }
}
</style>
