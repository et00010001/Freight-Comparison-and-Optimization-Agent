<template>
  <div class="app-shell">
    <AppHeader
      :has-custom-agent="hasCustomAgent"
      :custom-model="userCredentials.model"
      :custom-label="userCredentials.label"
      :custom-subtitle="userCredentials.subtitle"
      @open-settings="settingsVisible = true"
    />

    <AgentSettings
      v-model="settingsVisible"
      :credentials="userCredentials"
      @save="handleSaveCredentials"
      @disconnect="handleDisconnectAgent"
    />

    <section class="overview-strip">
      <StatisticsCard :statistics="statistics" @data-uploaded="handleDataUploaded" />
    </section>

    <main class="workbench">
      <section class="left-rail">
        <NLInput @parsed="handleNLParsed" @agent-update="handleAgentUpdate" :auth-headers="authHeaders" />

        <div class="panel agent-panel">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Agent 状态</p>
              <h2>执行过程</h2>
            </div>
            <el-tag :type="agentStatus.type" effect="plain">{{ agentStatus.text }}</el-tag>
          </div>

          <div class="agent-steps">
            <div
              v-for="item in agentSteps"
              :key="item.key"
              class="agent-step"
              :class="{ active: item.active, done: item.done }"
            >
              <span class="step-dot"></span>
              <div>
                <strong>{{ item.title }}</strong>
                <p>{{ item.desc }}</p>
              </div>
            </div>
          </div>

          <div v-if="agentSnapshot" class="agent-meta">
            <div>
              <span>解析来源</span>
              <strong>{{ sourceText(agentSnapshot.parse_source) }}</strong>
            </div>
            <div>
              <span>反馈来源</span>
              <strong>{{ sourceText(agentSnapshot.feedback_source) }}</strong>
            </div>
            <div>
              <span>意图</span>
              <strong>{{ agentSnapshot.intent || '-' }}</strong>
            </div>
            <div>
              <span>缺失字段</span>
              <strong>{{ missingText }}</strong>
            </div>
          </div>
        </div>
      </section>

      <section class="main-stage">
        <div class="stage-grid">
          <CompareForm
            v-model:form="form"
            :ports="ports"
            :loading="loading"
            @compare="handleCompare"
          />

          <RecommendCard
            v-if="result?.recommended_plan"
            :plan="result.recommended_plan"
          />

          <div v-else class="panel empty-recommendation">
            <p class="eyebrow">推荐方案</p>
            <h2 v-if="result && result.transfer_routes && result.transfer_routes.length > 0">转运方案</h2>
            <h2 v-else-if="result">未找到方案</h2>
            <h2 v-else>等待运输需求</h2>
            <p v-if="result && result.transfer_routes && result.transfer_routes.length > 0">
              未找到直达路线，已为您找到 {{ result.transfer_routes.length }} 条转运方案，请查看下方详情。
            </p>
            <p v-else-if="result">
              当前查询条件（{{ result.order_info?.orig_port }} → {{ result.order_info?.dest_port }}，{{ result.order_info?.weight }}kg）未匹配到可用方案，请调整重量或港口后重试。
            </p>
            <p v-else>输入自然语言需求或手动调整订单字段后，系统会匹配费率并给出推荐。</p>
          </div>
        </div>

        <FlowVisualization :step="flowStep" :result="result" :loading="loading" />

        <ResultTable v-if="result && (result.recommended_plan || (result.transfer_routes && result.transfer_routes.length > 0))" :result="result" />

        <ExportCard
          v-if="result && (result.recommended_plan || (result.transfer_routes && result.transfer_routes.length > 0))"
          :report="report"
          :exporting="exporting"
          :downloading-word="downloadingWord"
          @export="handleExport"
          @download-word="handleDownloadWord"
        />
      </section>

      <section class="history-rail">
        <HistoryPanel
          ref="historyPanelRef"
          :items="historyItems"
          @clear="handleClearHistory"
          @export-history="handleExportHistory"
        />
      </section>
    </main>

    <ChatPanel :auth-headers="authHeaders" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import AppHeader from './components/AppHeader.vue'
import AgentSettings from './components/AgentSettings.vue'
import StatisticsCard from './components/StatisticsCard.vue'
import CompareForm from './components/CompareForm.vue'
import ResultTable from './components/ResultTable.vue'
import RecommendCard from './components/RecommendCard.vue'
import ExportCard from './components/ExportCard.vue'
import NLInput from './components/NLInput.vue'
import FlowVisualization from './components/FlowVisualization.vue'
import ChatPanel from './components/ChatPanel.vue'
import HistoryPanel from './components/HistoryPanel.vue'

// ---- Agent 凭证管理 ----
const AGENT_CREDENTIALS_KEY = 'agent_credentials'
const settingsVisible = ref(false)

const loadCredentials = () => {
  try {
    const raw = localStorage.getItem(AGENT_CREDENTIALS_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return { api_key: '', base_url: '', model: '', label: '', subtitle: '' }
}

const userCredentials = ref(loadCredentials())
const hasCustomAgent = computed(() => !!(userCredentials.value.api_key && userCredentials.value.base_url))

const authHeaders = computed(() => {
  if (!hasCustomAgent.value) return {}
  return {
    'X-API-Key': userCredentials.value.api_key,
    'X-Base-URL': userCredentials.value.base_url,
    'X-Model': userCredentials.value.model || '',
  }
})

const handleSaveCredentials = (creds) => {
  userCredentials.value = creds
  localStorage.setItem(AGENT_CREDENTIALS_KEY, JSON.stringify(creds))
}

const handleDisconnectAgent = () => {
  userCredentials.value = { api_key: '', base_url: '', model: '', label: '', subtitle: '' }
  localStorage.removeItem(AGENT_CREDENTIALS_KEY)
}

const form = ref({
  weight: 100,
  orig_port: 'PORT08',
  dest_port: 'PORT09',
  max_days: null,
  priority: null
})

const ports = ref({ orig_ports: [], dest_ports: [] })
const statistics = ref({})
const result = ref(null)
const report = ref('')
const loading = ref(false)
const exporting = ref(false)
const downloadingWord = ref(false)
const flowStep = ref(0)
const agentSnapshot = ref(null)
const historyPanelRef = ref(null)

// 历史记录相关
const HISTORY_STORAGE_KEY = 'freight_history_items'
const MAX_HISTORY = 50

const loadHistoryFromStorage = () => {
  try {
    const raw = localStorage.getItem(HISTORY_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed
    }
  } catch { /* ignore */ }
  return []
}

const historyItems = ref(loadHistoryFromStorage())

const saveHistoryToStorage = () => {
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(historyItems.value))
  } catch { /* ignore */ }
}

const addHistoryItem = ({ userInput, replyType, message, order, result }) => {
  const item = {
    id: Date.now() + Math.random().toString(36).slice(2, 6),
    timestamp: Date.now(),
    userInput: userInput || '',
    replyType: replyType || 'general',
    message: message || '',
    order: order || null,
    result: result || '',
    summary: ''
  }

  // 生成摘要
  if (order?.weight && order?.orig_port && order?.dest_port) {
    const parts = [`${order.weight}kg`, order.orig_port, `→ ${order.dest_port}`]
    if (order.max_days) parts.push(`${order.max_days}天内`)
    item.summary = parts.join(' ')
  } else if (userInput) {
    item.summary = userInput.length > 40 ? userInput.slice(0, 40) + '...' : userInput
  } else {
    item.summary = message?.slice(0, 40) || '查询'
  }

  if (replyType === 'recommendation' && result) {
    item.result = result
  } else if (replyType === 'clarification') {
    item.result = '等待补充信息'
  } else if (replyType === 'no_result') {
    item.result = '未找到可用方案'
  }

  historyItems.value.unshift(item)
  if (historyItems.value.length > MAX_HISTORY) {
    historyItems.value = historyItems.value.slice(0, MAX_HISTORY)
  }
  saveHistoryToStorage()
}

const handleClearHistory = () => {
  historyItems.value = []
  saveHistoryToStorage()
}

const loadData = async () => {
  try {
    const [portsRes, statsRes] = await Promise.all([
      axios.get('/api/ports'),
      axios.get('/api/statistics')
    ])
    ports.value = portsRes.data
    statistics.value = statsRes.data
  } catch (e) {
    console.error('加载数据失败:', e)
  }
}

const handleDataUploaded = (data) => {
  // 上传成功后刷新统计数据和港口列表
  loadData()
  // 清空之前的比价结果
  result.value = null
  report.value = ''
  flowStep.value = 0
}

const agentStatus = computed(() => {
  const type = agentSnapshot.value?.reply_type
  if (type === 'processing') return { text: '处理中', type: 'primary' }
  if (type === 'clarification') return { text: '等待补充', type: 'warning' }
  if (type === 'recommendation') return { text: '已推荐', type: 'success' }
  if (type === 'no_result') return { text: '无匹配方案', type: 'danger' }
  if (type === 'general') return { text: '通用查询', type: 'info' }
  return { text: '待输入', type: 'info' }
})

const missingText = computed(() => {
  const fields = agentSnapshot.value?.missing_fields || []
  return fields.length ? fields.join(', ') : '无'
})

const agentSteps = computed(() => {
  const snapshot = agentSnapshot.value
  const replyType = snapshot?.reply_type
  const processing = replyType === 'processing'
  const hasOrder = Boolean(snapshot?.order)
  const hasRecommendation = Boolean(snapshot?.recommendation || result.value?.recommended_plan || (result.value?.transfer_routes && result.value.transfer_routes.length > 0))
  const hasFeedback = Boolean(snapshot?.message)

  return [
    {
      key: 'parse',
      title: '语言理解',
      desc: processing ? '正在解析自然语言和港口方向' : snapshot ? `识别意图：${snapshot.intent || '待确认'}` : '等待用户输入运输需求',
      done: Boolean(snapshot) && !processing,
      active: !snapshot || processing
    },
    {
      key: 'slots',
      title: '信息补全',
      desc: processing ? '即将检查重量、起运港、目的港是否完整' : replyType === 'clarification' ? `需要补充：${missingText.value}` : '订单字段已进入结构化检查',
      done: hasOrder && replyType !== 'clarification',
      active: replyType === 'clarification'
    },
    {
      key: 'match',
      title: '费率匹配',
      desc: processing ? '完整订单确认后会匹配 CSV 费率' : result.value ? (result.value.transfer_routes && result.value.transfer_routes.length > 0 ? `找到 ${result.value.transfer_routes.length} 条转运方案` : `找到 ${result.value.total_plans_found || 0} 个候选方案`) : '等待完整订单后查询 CSV 费率',
      done: Boolean(result.value),
      active: loading.value
    },
    {
      key: 'score',
      title: '方案评分',
      desc: hasRecommendation ? (result.value?.transfer_routes && result.value.transfer_routes.length > 0 ? '已找到转运方案并评分' : '已按当前权重生成推荐') : '成本、时效、服务评分待计算',
      done: hasRecommendation,
      active: Boolean(result.value) && !hasRecommendation
    },
    {
      key: 'feedback',
      title: '结果解释',
      desc: processing ? '等待解析结果后生成追问或推荐解释' : hasFeedback ? '已生成面向用户的自然语言反馈' : '等待推荐结果',
      done: hasFeedback && replyType !== 'clarification',
      active: hasFeedback && replyType === 'clarification'
    }
  ]
})

const sourceText = (source) => {
  if (source === 'llm') return 'LLM'
  if (source === 'fallback') return '规则兜底'
  if (source === 'template') return '模板'
  return '-'
}

const lastUserInput = ref('')

const handleAgentUpdate = (data) => {
  if (!data) return

  // 捕获用户输入文本
  if (data._userText) {
    lastUserInput.value = data._userText
  }

  // 新查询开始时清除旧的比价结果，避免旧推荐残留误导用户
  if (data.reply_type === 'processing' || data.reply_type === 'no_result') {
    result.value = null
    report.value = ''
    flowStep.value = 0
  }

  agentSnapshot.value = data
  if (data.reply_type === 'clarification') flowStep.value = 1

  // 记录历史（排除 processing 状态）
  if (data.reply_type && data.reply_type !== 'processing') {
    addHistoryItem({
      userInput: lastUserInput.value,
      replyType: data.reply_type,
      message: data.message,
      order: data.order,
      result: data.reply_type === 'recommendation'
        ? `${data.recommendation?.carrier || ''} $${data.recommendation?.total_cost?.toFixed(2) || ''}`
        : ''
    })
  }
}

const handleNLParsed = (data) => {
  if (data.weight) form.value.weight = data.weight
  if (data.orig_port) form.value.orig_port = data.orig_port
  if (data.dest_port) form.value.dest_port = data.dest_port
  if (data.max_days) form.value.max_days = data.max_days
  if (data.priority) form.value.priority = data.priority
  flowStep.value = 1

  if (data.priority === 'time') {
    ElMessage.info('已识别为时效优先模式')
  } else if (data.priority === 'cost') {
    ElMessage.info('已识别为成本优先模式')
  }

  if (data.weight && data.orig_port && data.dest_port) {
    setTimeout(() => {
      handleCompare()
    }, 500)
  }
}

const handleCompare = async () => {
  if (!form.value.weight || !form.value.orig_port || !form.value.dest_port) {
    ElMessage.warning('请填写必填项')
    return
  }

  loading.value = true
  result.value = null
  report.value = ''
  flowStep.value = 2

  try {
    const requestData = {
      weight: form.value.weight,
      orig_port: form.value.orig_port,
      dest_port: form.value.dest_port,
      max_days: form.value.max_days || null,
      priority: form.value.priority || null
    }
    const res = await axios.post('/api/compare', requestData)
    result.value = res.data
    flowStep.value = 4

    const isOverweight = result.value.available_plans?.some(plan => !plan.is_exact_match)
    const hasNoRecommendation = !result.value.recommended_plan

    const hasTransferRoutes = result.value.transfer_routes && result.value.transfer_routes.length > 0

    if (hasNoRecommendation && isOverweight) {
      ElMessage.warning('重量过重，未找到可用方案')
    } else if (isOverweight) {
      ElMessage.warning('重量超标，建议考虑分批运输')
    } else if (hasNoRecommendation && hasTransferRoutes) {
      ElMessage.success(`找到 ${result.value.transfer_routes.length} 条转运方案`)
    } else {
      const priorityText = form.value.priority === 'time' ? '时效优先' :
        form.value.priority === 'cost' ? '成本优先' : '均衡模式'
      ElMessage.success(`已生成推荐方案：${priorityText}`)
    }
  } catch (e) {
    ElMessage.error('推荐失败: ' + (e.response?.data?.detail || e.message))
    flowStep.value = 2
  } finally {
    loading.value = false
  }
}

const handleExport = async () => {
  exporting.value = true
  try {
    const res = await axios.post('/api/export', {
      weight: form.value.weight,
      orig_port: form.value.orig_port,
      dest_port: form.value.dest_port,
      max_days: form.value.max_days || null,
      priority: form.value.priority || null,
      feedback: agentSnapshot.value?.message || null
    })
    report.value = res.data.report
    ElMessage.success('报告生成成功')
  } catch (e) {
    ElMessage.error('导出失败')
  } finally {
    exporting.value = false
  }
}

const handleDownloadWord = async () => {
  downloadingWord.value = true
  try {
    const res = await axios.post('/api/export_docx', {
      weight: form.value.weight,
      orig_port: form.value.orig_port,
      dest_port: form.value.dest_port,
      max_days: form.value.max_days || null,
      priority: form.value.priority || null,
      feedback: agentSnapshot.value?.message || null
    }, { responseType: 'blob' })

    const url = window.URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url
    a.download = `比价报告_${form.value.orig_port}_${form.value.dest_port}_${form.value.weight}kg.docx`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success('Word文档已下载')
  } catch (e) {
    ElMessage.error('Word文档生成失败')
  } finally {
    downloadingWord.value = false
  }
}

const handleExportHistory = async () => {
  if (historyItems.value.length === 0) {
    ElMessage.warning('暂无历史记录')
    return
  }
  try {
    const res = await axios.post('/api/export_history_docx', {
      items: historyItems.value
    }, { responseType: 'blob' })

    const url = window.URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url
    a.download = '历史查询记录.docx'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success('历史记录已导出')
  } catch (e) {
    ElMessage.error('历史记录导出失败')
  }
}

onMounted(loadData)
</script>

<style>
:root {
  color: #1f2937;
  background: #eef3f8;
  font-family: Inter, "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

* {
  box-sizing: border-box;
  letter-spacing: 0;
  margin: 0;
  padding: 0;
}

body {
  min-width: 320px;
  background:
    linear-gradient(180deg, #f8fafc 0%, #eef3f8 42%, #e9eef5 100%);
}

.app-shell {
  min-height: 100vh;
  padding: 22px;
}

.overview-strip {
  max-width: 1700px;
  margin: 14px auto 18px;
}

.workbench {
  display: grid;
  grid-template-columns: minmax(360px, 430px) minmax(0, 1fr) 300px;
  gap: 18px;
  max-width: 1700px;
  margin: 0 auto;
  align-items: start;
}

.left-rail,
.main-stage {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.left-rail {
  position: sticky;
  top: 16px;
}

.history-rail {
  position: sticky;
  top: 16px;
}

.stage-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
  gap: 16px;
  align-items: stretch;
}

.panel {
  background: #ffffff;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  padding: 18px;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.05);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}

.panel h2 {
  color: #1f2937;
  font-size: 18px;
  line-height: 1.25;
}

.eyebrow {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
  margin-bottom: 4px;
}

.agent-steps {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.agent-step {
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 10px;
  color: #64748b;
}

.agent-step strong {
  color: #334155;
  display: block;
  font-size: 14px;
  margin-bottom: 2px;
}

.agent-step p {
  font-size: 12px;
  line-height: 1.45;
}

.step-dot {
  width: 10px;
  height: 10px;
  margin-top: 4px;
  border-radius: 999px;
  border: 2px solid #cbd5e1;
  background: #fff;
}

.agent-step.done .step-dot {
  background: #0f766e;
  border-color: #0f766e;
}

.agent-step.active .step-dot {
  background: #2563eb;
  border-color: #2563eb;
  box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.12);
}

.agent-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 16px;
}

.agent-meta div {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 10px;
}

.agent-meta span {
  color: #64748b;
  display: block;
  font-size: 12px;
  margin-bottom: 4px;
}

.agent-meta strong {
  color: #1e293b;
  font-size: 13px;
  word-break: break-word;
}

.empty-recommendation {
  min-height: 100%;
}

.empty-recommendation h2 {
  margin-bottom: 8px;
}

.empty-recommendation p:last-child {
  color: #64748b;
  font-size: 14px;
  line-height: 1.6;
}

.el-button {
  border-radius: 6px;
}

.el-card,
.el-input__wrapper,
.el-textarea__inner,
.el-select__wrapper {
  border-radius: 6px;
}

@media (max-width: 1100px) {
  .workbench,
  .stage-grid {
    grid-template-columns: 1fr;
  }

  .left-rail,
  .history-rail {
    position: static;
  }
}

@media (max-width: 720px) {
  .app-shell {
    padding: 12px;
  }

  .agent-meta {
    grid-template-columns: 1fr;
  }
}
</style>
