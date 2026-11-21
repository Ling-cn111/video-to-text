'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, ExternalLink, FlaskConical, KeyRound, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useApiSettingsOpenSignal } from '@/hooks/use-api-settings'
import { notifyByokChanged } from '@/hooks/use-byok-status'
import { openExternal } from '@/lib/external-link'
import { IS_MOBILE_SHELL } from '@/lib/runtime'
import { MobileUpdateSection } from '@/components/layout/mobile-update-section'
import { api } from '@/lib/api'
import { clearSummaryCache } from '@/stores/task-store'
import {
  configLabel,
  listConfigs,
  activateConfigEntry,
  clearAllByokConfigs,
  deleteConfigEntry,
  maskKey,
  newConfigId,
  readActiveConfigEntry,
  saveConfigEntry,
  LLM_PROVIDER_PRESETS,
  validateBaseUrlFormat,
  validateKeyFormat,
  validateModelFormat,
  type ByokConfigEntry,
  type LlmProviderPreset,
} from '@/lib/byok'

/**
 * 识别当前草稿对应哪个预设：地址与模型都对得上才算。
 *
 * 为什么不存「用户选了哪个服务商」：服务商只是填空助手，真正生效的是地址与
 * 模型这两个值。存了反而会不一致——用户选了 DeepSeek 又把地址改成自己的代理，
 * 界面还显示 DeepSeek，会让人以为请求打到了官方。这里由值反推，永远不会骗人。
 */
function matchPreset(baseUrl: string | null | undefined): LlmProviderPreset | null {
  if (!baseUrl) return null
  const normalized = baseUrl.replace(/\/+$/, '')
  return (
    LLM_PROVIDER_PRESETS.find((preset) => preset.baseUrl && preset.baseUrl.replace(/\/+$/, '') === normalized) ?? null
  )
}

function presetHost(baseUrl: string): string {
  try {
    return new URL(baseUrl).hostname
  } catch {
    return baseUrl
  }
}

/** 面板里的一行说明文字 */
function FieldHint({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <p className="text-xs text-muted-foreground" data-testid={testId}>
      {children}
    </p>
  )
}

export interface ApiSettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** 表单编辑目标：null = 新建一套；string = 更新已有条目 */
type EditingTarget = string | null

export function ApiSettingsDialog({ open, onOpenChange }: ApiSettingsDialogProps) {
  // 初始为空，挂载（open 变化）后再读 localStorage：避免 SSR hydration 不一致
  const [configs, setConfigs] = useState<ByokConfigEntry[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<EditingTarget>(null)
  const [draftBaseUrl, setDraftBaseUrl] = useState('')
  const [draftModel, setDraftModel] = useState('')
  const [draftKey, setDraftKey] = useState('')
  const [keyDirty, setKeyDirty] = useState(false)
  // 切换服务商后置位：旧 Key 属于别家，此时必须显示空的密码输入框而不是旧 Key 的掩码
  const [keyInvalidated, setKeyInvalidated] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [switchHint, setSwitchHint] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string; latencyMs?: number } | null>(null)

  // 任意位置（结果页总结卡、首页提示）都能把这个面板叫起来
  const handleExternalOpen = useCallback(() => onOpenChange(true), [onOpenChange])
  useApiSettingsOpenSignal(handleExternalOpen)

  const refreshFromStorage = useCallback(() => {
    const entries = listConfigs()
    setConfigs(entries)
    const active = readActiveConfigEntry()
    setActiveId(active?.id ?? null)
    return { entries, active }
  }, [])

  // 打开面板：列表 + 表单回填「当前生效配置」（回归 22：刷新后输入框回填、key 掩码）
  useEffect(() => {
    if (!open) return
    const { active } = refreshFromStorage()
    setEditingId(active?.id ?? null)
    setDraftBaseUrl(active?.llmBaseUrl ?? '')
    setDraftModel(active?.llmModel ?? '')
    setDraftKey('')
    setKeyDirty(false)
    setKeyInvalidated(false)
    setError(null)
    setSwitchHint(null)
    setTestResult(null)
    setTesting(false)
  }, [open, refreshFromStorage])

  const activePreset = useMemo(() => matchPreset(draftBaseUrl), [draftBaseUrl])
  const keyUrl = activePreset?.keyUrl || ''
  const keyUrlName = activePreset?.name || '当前服务商'

  /** 编辑目标的已存 Key（未手动输入新 Key 时沿用） */
  const editingEntry = editingId ? configs.find((e) => e.id === editingId) ?? null : null
  const effectiveKey = keyDirty && draftKey ? draftKey : editingEntry?.key ?? ''

  function resetFormToNew() {
    setEditingId(null)
    setDraftBaseUrl('')
    setDraftModel('')
    setDraftKey('')
    setKeyDirty(false)
    setKeyInvalidated(false)
    setError(null)
    setSwitchHint(null)
    setTestResult(null)
  }

  function startEdit(entry: ByokConfigEntry) {
    setEditingId(entry.id)
    setDraftBaseUrl(entry.llmBaseUrl ?? '')
    setDraftModel(entry.llmModel)
    setDraftKey('')
    setKeyDirty(false)
    setKeyInvalidated(false)
    setError(null)
    setSwitchHint(null)
    setTestResult(null)
  }

  function applyPreset(preset: LlmProviderPreset) {
    setError(null)
    // 选「其他 / 自建」时不清空用户已填的地址与模型：他多半是想继续改
    if (preset.id === 'custom') return
    const previousHost = draftBaseUrl ? presetHost(draftBaseUrl) : ''
    setDraftBaseUrl(preset.baseUrl)
    setDraftModel(preset.models[0] ?? '')
    // 痛点 4：换服务商必须换该服务商的 Key——旧 Key 是别的服务商发的，留着只会
    // 在总结时 401。自动清空草稿并明确提示（若编辑的是已存配置，旧 Key 仍在
    // 条目里，保存时未输入新 Key 会沿用并给出强提示）。
    const nextHost = presetHost(preset.baseUrl)
    if (nextHost && previousHost && nextHost !== previousHost) {
      setDraftKey('')
      setKeyDirty(false)
      setKeyInvalidated(true)
      setSwitchHint(`已切换到「${preset.name}」：请填写该服务商的 API Key（原 Key 属于其他服务商）`)
    }
  }

  function handleSave() {
    const keyResult = !keyDirty && editingEntry ? { ok: true as const, value: editingEntry.key } : validateKeyFormat(draftKey)
    if (!keyResult.ok) {
      setError(keyResult.reason)
      return
    }
    const urlResult = validateBaseUrlFormat(draftBaseUrl)
    if (!urlResult.ok) {
      setError(urlResult.reason)
      return
    }
    const modelResult = validateModelFormat(draftModel)
    if (!modelResult.ok) {
      setError(modelResult.reason)
      return
    }
    // 填了地址却没填模型：站点默认模型（如 deepseek-flash）在别家服务商那里
    // 不存在，发过去只会得到一句「model not found」。这里就拦住。
    if (urlResult.value && !modelResult.value) {
      setError('填写了自定义接口地址，还需要填写模型名（例如 deepseek-chat / gpt-4o-mini）')
      return
    }
    // 填了地址却没填 Key：Key 的回落目标是站点自己的 Key，不拦就会把站点凭据
    // 发往用户指定的地址。地址与 Key 必须同源。
    if (urlResult.value && !keyResult.value) {
      setError('填写了自定义接口地址，还需要填写该服务商的 API Key：站点不会把自己的 Key 发往你指定的地址')
      return
    }

    const entry: ByokConfigEntry = {
      id: editingId ?? newConfigId(),
      llmBaseUrl: urlResult.value || null,
      llmModel: modelResult.value,
      key: keyResult.value,
    }
    let saved = entry
    try {
      // 保存即启用：用户刚填完的这套就是要用的（要切别的走列表的「使用」）
      saveConfigEntry(entry, true)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
      return
    }
    // 同步本地列表视图
    const { entries, active } = refreshFromStorage()
    setEditingId(active?.id ?? null)
    setConfigs(entries)
    saved = entries.find((e) => e.id === entry.id) ?? saved
    setDraftBaseUrl(saved.llmBaseUrl ?? '')
    setDraftModel(saved.llmModel)
    setDraftKey('')
    setKeyDirty(false)
    setKeyInvalidated(false)
    setError(null)
    setSwitchHint(null)
    setTestResult(null)
    // 换了服务商/模型/Key，之前缓存的总结就不再是「这份配置的结果」了。
    // 不清掉的话，用户点「重试总结」会命中旧缓存直接返回，看起来像新配置通了。
    clearSummaryCache()
    // 通知同页的能力门禁重算：刚填了配置，结果页那个灰掉的「生成总结」应当立刻可用
    notifyByokChanged()
    toast.success('配置已保存并启用，只存在这台设备的浏览器里')
    // 痛点 4 兜底：切换服务商后沿用了旧 Key 时，给出可操作的强提示
    if (switchHint && !keyDirty) {
      toast.warning('注意：沿用了之前的 Key。若它不属于当前服务商，总结会失败——可点「测试」验证', { duration: 6000 })
    }
  }

  function handleActivate(id: string) {
    const target = activateConfigEntry(id)
    if (!target) return
    refreshFromStorage()
    // 切换配置 = 换了服务商/模型/Key 组合，旧总结缓存同理作废（回归 25 语义延伸）
    clearSummaryCache()
    notifyByokChanged()
    toast.success(`已切换到「${configLabel(target, configs)}」`)
  }

  function handleDelete(id: string) {
    const wasActive = id === activeId
    deleteConfigEntry(id)
    const { entries, active } = refreshFromStorage()
    setConfigs(entries)
    setActiveId(active?.id ?? null)
    if (wasActive) {
      // 删的是生效配置：回到未配置态，表单同步清空（对应回归 14c 的「清除」语义延伸）
      setEditingId(null)
      setDraftBaseUrl('')
      setDraftModel('')
      setDraftKey('')
      setKeyDirty(false)
      setKeyInvalidated(false)
    } else if (editingId === id) {
      setEditingId(null)
      resetFormToNew()
    }
    clearSummaryCache()
    notifyByokChanged()
    toast.success('已删除该配置')
  }

  function handleClear() {
    clearAllByokConfigs()
    setConfigs([])
    setActiveId(null)
    resetFormToNew()
    setKeyInvalidated(false)
    // 清配置后旧总结同样不该再被当成「当前配置的结果」复用
    clearSummaryCache()
    notifyByokChanged()
    toast.success('已清除全部 API 配置')
  }

  async function handleTest() {
    const urlResult = validateBaseUrlFormat(draftBaseUrl)
    const modelResult = validateModelFormat(draftModel)
    if (!urlResult.ok || !urlResult.value) {
      toast.error('测试前请先填写接口地址')
      return
    }
    if (!modelResult.ok || !modelResult.value) {
      toast.error('测试前请先填写模型名')
      return
    }
    const keyForTest = keyDirty && draftKey ? draftKey : editingEntry?.key ?? ''
    if (!keyForTest) {
      toast.error('测试前请先填写 API Key')
      return
    }
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.testLlmConfig({ llmBaseUrl: urlResult.value, llmModel: modelResult.value }, keyForTest)
      if (result.ok) {
        setTestResult({ ok: true, message: `可用：模型 ${result.model ?? modelResult.value}`, latencyMs: result.latencyMs })
      } else {
        setTestResult({ ok: false, message: result.message ?? '测试失败', latencyMs: result.latencyMs })
      }
    } catch (err) {
      // 校验类失败（SSRF / 缺字段）走标准错误形状：解析出可读文案展示
      const message = err instanceof Error ? err.message : '测试失败'
      setTestResult({ ok: false, message })
    } finally {
      setTesting(false)
    }
  }

  const showingStoredKey = Boolean(editingEntry?.key) && !keyDirty && !keyInvalidated
  const hasAnyConfig = configs.length > 0
  const canTest = Boolean(draftBaseUrl.trim() && draftModel.trim() && (keyDirty ? draftKey : editingEntry?.key))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto" data-testid="api-settings-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="size-4" aria-hidden />
            API 设置
          </DialogTitle>
          <DialogDescription>
            填自己的 AI 总结服务，就用你自己的额度，不占用站点配额。可保存多套配置随时切换；测试按钮会发一次极短的真实请求验证可用性。语音识别用的是免费模型，不需要填。
          </DialogDescription>
        </DialogHeader>

        {/* 已保存的配置列表：名称（模型名）+ 主机 + 掩码 Key + 使用/编辑/删除 */}
        {hasAnyConfig ? (
          <div className="space-y-2" data-testid="byok-config-list">
            <Label className="text-xs text-muted-foreground">已保存的配置</Label>
            {configs.map((entry) => {
              const isActive = entry.id === activeId
              return (
                <div
                  key={entry.id}
                  className={`flex items-center gap-2 rounded-lg border p-2.5 ${isActive ? 'border-primary/60 bg-primary/5' : ''}`}
                  data-testid="byok-config-item"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-sm font-medium">{configLabel(entry, configs)}</span>
                      {isActive ? (
                        <span
                          className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-600 dark:text-emerald-400"
                          data-testid="byok-config-active-badge"
                        >
                          <Check className="mr-0.5 inline size-3" aria-hidden />
                          使用中
                        </span>
                      ) : null}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {entry.llmBaseUrl ? presetHost(entry.llmBaseUrl) : '跟随站点预设'} · Key {maskKey(entry.key)}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {!isActive ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => handleActivate(entry.id)}
                        data-testid={`byok-config-use-${entry.id}`}
                        aria-label={`切换到 ${entry.llmModel}`}
                      >
                        使用
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => startEdit(entry)}
                      data-testid={`byok-config-edit-${entry.id}`}
                      aria-label={`编辑 ${entry.llmModel}`}
                    >
                      编辑
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-muted-foreground"
                      onClick={() => handleDelete(entry.id)}
                      data-testid={`byok-config-delete-${entry.id}`}
                      aria-label={`删除 ${entry.llmModel}`}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              )
            })}
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1 px-2 text-xs"
              onClick={resetFormToNew}
              data-testid="byok-config-add"
            >
              <Plus className="size-3.5" aria-hidden />
              新增一套
            </Button>
          </div>
        ) : null}

        <div className="space-y-3">
          <div className="space-y-2 rounded-lg border p-4" data-testid="byok-llm">
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-0.5">
                <span className="text-sm font-medium">{editingId ? '编辑配置' : '新增配置'}</span>
                <FieldHint>兼容 OpenAI chat/completions 协议的服务商都支持</FieldHint>
              </div>
              {effectiveKey ? (
                <span className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-600 dark:text-emerald-400">
                  已启用
                </span>
              ) : (
                <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                  未填写
                </span>
              )}
            </div>

            {/* 服务商预设：只是填空助手，不是白名单 */}
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">服务商</Label>
              <div className="flex flex-wrap gap-1.5" data-testid="byok-provider-presets">
                {LLM_PROVIDER_PRESETS.map((preset) => {
                  const active = preset.id === 'custom' ? !activePreset : activePreset?.id === preset.id
                  return (
                    <Button
                      key={preset.id}
                      type="button"
                      size="sm"
                      variant={active ? 'default' : 'outline'}
                      className="h-7 px-2 text-xs"
                      onClick={() => applyPreset(preset)}
                      data-testid={`byok-preset-${preset.id}`}
                      aria-pressed={active}
                    >
                      {preset.name}
                    </Button>
                  )
                })}
              </div>
              {switchHint ? (
                <p className="text-xs text-amber-600 dark:text-amber-400" data-testid="byok-switch-hint">
                  {switchHint}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="byok-base-url" className="text-xs text-muted-foreground">
                接口地址
              </Label>
              <Input
                id="byok-base-url"
                value={draftBaseUrl}
                onChange={(event) => {
                  setDraftBaseUrl(event.target.value)
                  setError(null)
                }}
                placeholder="https://api.deepseek.com/v1"
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-xs"
                data-testid="byok-llm-base-url-input"
              />
              <FieldHint testId="byok-llm-base-url-hint">
                留空则使用本站配置的服务商。填自建 / 中转服务时注意：不要填本机或内网地址，后端会拒绝。
              </FieldHint>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="byok-model" className="text-xs text-muted-foreground">
                模型名
              </Label>
              {/* combobox：预设家的常用模型列表 + 仍可自由输入（datalist 原生行为） */}
              {activePreset && activePreset.models.length > 0 ? (
                <datalist id="byok-model-options">
                  {activePreset.models.map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              ) : null}
              <Input
                id="byok-model"
                value={draftModel}
                onChange={(event) => {
                  setDraftModel(event.target.value)
                  setError(null)
                }}
                placeholder="deepseek-chat"
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-xs"
                data-testid="byok-llm-model-input"
                list={activePreset && activePreset.models.length > 0 ? 'byok-model-options' : undefined}
              />
              <FieldHint>填了自定义接口地址时必须填。模型名以服务商的文档为准。</FieldHint>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="byok-llm" className="text-xs text-muted-foreground">
                API Key
              </Label>
              {showingStoredKey ? (
                <div className="flex items-center gap-2">
                  <code
                    className="flex-1 truncate rounded-md border bg-muted/40 px-3 py-1.5 font-mono text-sm"
                    data-testid="byok-llm-masked"
                  >
                    {maskKey(editingEntry?.key as string)}
                  </code>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setKeyDirty(true)
                      setDraftKey('')
                    }}
                    data-testid="byok-llm-replace"
                  >
                    更换
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={handleClear}
                    data-testid="byok-llm-clear"
                    aria-label="清除全部 API 配置"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ) : (
                <>
                  <Input
                    id="byok-llm"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="粘贴你的 API Key"
                    value={draftKey}
                    onChange={(event) => {
                      setDraftKey(event.target.value)
                      setKeyDirty(true)
                      setError(null)
                    }}
                    data-testid="byok-llm-input"
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? 'byok-llm-error' : undefined}
                  />
                </>
              )}
              {error ? (
                <p className="text-xs text-destructive" data-testid="byok-llm-error" role="alert">
                  {error}
                </p>
              ) : null}
              {/* 测试按钮：对当前草稿（地址 + 模型 + Key）发一次极短真实请求 */}
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 gap-1 px-2 text-xs"
                  onClick={() => void handleTest()}
                  disabled={!canTest || testing}
                  data-testid="byok-test-button"
                >
                  <FlaskConical className="size-3.5" aria-hidden />
                  {testing ? '测试中…' : '测试可用性'}
                </Button>
                {testResult ? (
                  <span
                    className={`truncate text-xs ${testResult.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive'}`}
                    data-testid="byok-test-result"
                  >
                    {testResult.message}
                    {testResult.ok && typeof testResult.latencyMs === 'number' ? `（${testResult.latencyMs} ms）` : ''}
                  </span>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {/* 没有 keyUrl 时（「其他 / 自建」）不渲染这个按钮：给一个到不了的
                    链接比不给更差，用户会以为点错了。 */}
                {keyUrl ? (
                  <button
                    type="button"
                    onClick={() => void openExternal(keyUrl)}
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                    data-testid="byok-key-url"
                  >
                    {keyUrlName} 取 Key
                    <ExternalLink className="size-3" aria-hidden />
                  </button>
                ) : (
                  <span className="text-xs text-muted-foreground" data-testid="byok-key-url-absent">
                    自建 / 中转服务的 Key 请从服务商后台获取
                  </span>
                )}
                {activePreset?.keyHint ? (
                  <span className="text-xs text-muted-foreground">{activePreset.keyHint}</span>
                ) : null}
              </div>
            </div>
          </div>
        </div>

        <div className="flex gap-2 rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p data-testid="byok-storage-note">
            填写后只保存在这台设备的浏览器里，可保存多套配置随时切换。生成总结时，生效配置的 Key 会随请求发给本站后端，后端用它调用你选的
            AI 服务商，用完即丢弃：不写入任务记录、不写进日志、不随任务存下来；「测试可用性」同理，发一次极短请求后即丢弃。公用电脑上用完建议清除。
          </p>
        </div>

        {/* 只有 Android 壳需要「检查更新」：网页刷新即最新，桌面端在顶栏有独立入口 */}
        {IS_MOBILE_SHELL ? <MobileUpdateSection /> : null}

        <DialogFooter className="items-center justify-between gap-2 sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleClear}
            data-testid="byok-clear-all"
            disabled={!hasAnyConfig}
          >
            清除全部
          </Button>
          <div className="flex gap-2">
            <Button type="button" onClick={handleSave} data-testid="byok-llm-save">
              保存并启用
            </Button>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} data-testid="byok-done">
              完成
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
