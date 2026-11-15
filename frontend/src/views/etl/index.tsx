import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/etl'
const WATERMARK_ENDPOINT = '/api/etl/watermark'
const columns = [
  "任务编号",
  "任务名称",
  "输入源",
  "转换步骤",
  "输出目标",
  "调度周期",
  "依赖关系",
  "任务状态"
]
const actions = [
  "启动任务",
  "暂停任务",
  "归档任务"
]
const statuses = [
  "草稿",
  "运行中",
  "已暂停",
  "已完成"
]
const initialStats = [
  {
    "label": "准备任务总数",
    "value": 0
  },
  {
    "label": "待消费异动条目",
    "value": 0
  },
  {
    "label": "迁移失败区间",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type WatermarkFeed = {
  since?: string
  items?: Row[]
  total?: number
  失败区间?: { 记录编号: string; 原因: string }[]
}

export default function EtlView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [feed, setFeed] = useState<WatermarkFeed>({})
  const [notice, setNotice] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const stats = useMemo(() => [
    { label: initialStats[0].label, value: total },
    { label: initialStats[1].label, value: feed.total ?? 0 },
    { label: initialStats[2].label, value: (feed["失败区间"] ?? []).length },
  ], [total, feed])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const [listResponse, feedResponse] = await Promise.all([
        request(`${ENDPOINT}?${query}`),
        request(WATERMARK_ENDPOINT),
      ])
      if (!listResponse.ok) {
        throw new Error('准备任务列表读取失败')
      }
      const payload = await listResponse.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      if (feedResponse.ok) {
        setFeed(await feedResponse.json())
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据准备流水线列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function consumeWatermark() {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${WATERMARK_ENDPOINT}/consume`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      if (!response.ok) {
        throw new Error('异动水位消费失败，请稍后重试')
      }
      const result = await response.json()
      setNotice(result.message ?? '异动水位已消费')
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '异动水位消费失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('数据准备流水线动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据准备流水线操作失败')
    }
  }

  return (
    <section className="page" data-module="etl">
      <header className="page-head">
        <div>
          <h2>数据准备流水线管理</h2>
          <p className="page-desc">维护数据准备的转换步骤、调度配置与依赖管理；流水线按水位消费迁移后的异动条目，失败区间保留原记录不再重复消费。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('准备任务登记入口尚未接入审批流')}>
            登记准备任务
          </button>
          <button className="btn" type="button" onClick={() => void consumeWatermark()}>
            消费异动水位
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出数据准备流水线清单
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
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
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
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无数据准备流水线数据，可先登记准备任务</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">待消费的异动条目（按水位）</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>指标名称</th>
            <th>异动方向</th>
            <th>异动幅度</th>
            <th>归因维度</th>
            <th>归因结论</th>
            <th>水位</th>
          </tr>
        </thead>
        <tbody>
          {(feed.items ?? []).map((item) => (
            <tr key={String(item["记录编号"])}>
              <td>{item["记录编号"] ?? '—'}</td>
              <td>{item["指标名称"] ?? '—'}</td>
              <td>{item["异动方向"] ?? '—'}</td>
              <td>{item["异动幅度"] ?? '—'}</td>
              <td>{item["归因维度"] ?? '—'}</td>
              <td>{item["归因结论"] ?? '—'}</td>
              <td>{item["水位"] ?? '—'}</td>
            </tr>
          ))}
          {(feed.items ?? []).length === 0 && (
            <tr>
              <td className="empty-state" colSpan={7}>当前水位之后没有待消费的异动条目</td>
            </tr>
          )}
        </tbody>
      </table>

      {(feed["失败区间"] ?? []).length > 0 && (
        <>
          <h3 className="section-title">迁移失败区间（原记录保留在旧窗口表）</h3>
          <table className="data-table">
            <thead>
              <tr><th>记录编号</th><th>失败原因</th></tr>
            </thead>
            <tbody>
              {(feed["失败区间"] ?? []).map((item) => (
                <tr key={item.记录编号}>
                  <td>{item.记录编号}</td>
                  <td>{item.原因}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <footer className="page-foot">
        <span>共 {total} 条数据准备流水线记录 · 消费水位 {feed.since || '未推进'}</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
