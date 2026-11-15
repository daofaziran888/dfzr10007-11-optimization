import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/anomaly'
const LEGACY_ENDPOINT = '/api/anomaly/legacy'
const MIGRATION_ENDPOINT = '/api/anomaly/migrations'
const columns = [
  "记录编号",
  "指标名称",
  "异动方向",
  "异动幅度",
  "归因维度",
  "归因结论",
  "异动时间",
  "水位",
  "处理建议"
]
const legacyColumns = ["窗口编号", "指标名称", "异动描述", "迁移状态", "失败原因"]
const actions = [
  "开始分析",
  "确认归因",
  "关闭异动"
]
const statuses = [
  "待分析",
  "分析中",
  "已归因",
  "已关闭"
]

type Row = Record<string, string | number | null>
type LegacyRow = Record<string, string | number | null>

type Summary = {
  "异动条目总数"?: number
  "已归因"?: number
  "分析中"?: number
  "旧窗口数"?: number
  "已迁移"?: number
  "迁移失败"?: number
  "最新水位"?: string
}

type MigrationResult = {
  ok: boolean
  message: string
  entry?: { summary?: { 迁移?: number; 跳过?: number; 失败?: number } }
}

export default function AnomalyView() {
  const [rows, setRows] = useState<Row[]>([])
  const [legacyRows, setLegacyRows] = useState<LegacyRow[]>([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState<Summary>({})
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => ["keyword", "status", "dimension"], [])
  const filterLabels: Record<string, string> = {
    keyword: '记录编号/指标名称',
    status: '状态',
    dimension: '归因维度',
  }

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const [listResponse, legacyResponse, summaryResponse] = await Promise.all([
        request(`${ENDPOINT}?${query}`),
        request(LEGACY_ENDPOINT),
        request(`${MIGRATION_ENDPOINT}/summary`),
      ])
      if (!listResponse.ok) {
        throw new Error('异动条目列表读取失败')
      }
      const payload = await listResponse.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      if (legacyResponse.ok) {
        setLegacyRows((await legacyResponse.json()).items ?? [])
      }
      if (summaryResponse.ok) {
        setSummary(await summaryResponse.json())
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标异动归因列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('指标异动归因动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标异动归因操作失败')
    }
  }

  async function runMigration(codes?: string[]) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(MIGRATION_ENDPOINT, {
        method: 'POST',
        body: JSON.stringify(codes ? { codes } : {}),
      })
      if (!response.ok) {
        throw new Error('旧异动窗口迁移失败，请稍后重试')
      }
      const result = (await response.json()) as MigrationResult
      setNotice(`${result.message}（迁移 ${result.entry?.summary?.迁移 ?? 0}，跳过 ${result.entry?.summary?.跳过 ?? 0}）`)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '旧异动窗口迁移失败')
    }
  }

  const pendingRetry = legacyRows.filter((row) => row["迁移状态"] === "失败").map((row) => String(row["窗口编号"]))
  const stats = [
    { label: "异动条目总数", value: summary["异动条目总数"] ?? 0 },
    { label: "已迁移窗口", value: summary["已迁移"] ?? 0 },
    { label: "迁移失败区间", value: summary["迁移失败"] ?? 0 },
  ]

  return (
    <section className="page" data-module="anomaly">
      <header className="page-head">
        <div>
          <h2>指标异动归因管理</h2>
          <p className="page-desc">旧异动窗口已拆成独立条目，时间、方向、归因幅度与水位分开存储；旧窗口原表只读保留，供追溯和失败区间重试。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => void runMigration()}>
            回填旧异动窗口
          </button>
          {pendingRetry.length > 0 && (
            <button className="btn" type="button" onClick={() => void runMigration(pendingRetry)}>
              重试失败区间（{pendingRetry.length}）
            </button>
          )}
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出指标异动归因清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
        <article className="stat-card">
          <span className="stat-label">最新水位</span>
          <strong className="stat-value">{summary["最新水位"] || '—'}</strong>
        </article>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{filterLabels[field]}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${filterLabels[field]}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => (
                <td key={column}>{column === "异动幅度" && row[column] !== null ? `${row[column]}%` : (row[column] ?? '—')}</td>
              ))}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无指标异动条目，可先回填旧异动窗口</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">旧异动窗口原表（只读保留，用于追溯与失败重试）</h3>
      <table className="data-table">
        <thead>
          <tr>{legacyColumns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {legacyRows.map((row) => (
            <tr key={String(row.id)}>
              {legacyColumns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
            </tr>
          ))}
          {legacyRows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={legacyColumns.length}>旧窗口原表为空</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条指标异动条目 · 旧窗口 {summary["旧窗口数"] ?? 0} 条</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
