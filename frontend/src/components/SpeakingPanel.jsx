import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { fmtTime } from '../utils.js'

/**
 * 发言时长面板（speaking time）。
 *
 * Props:
 *   projectId  当前项目 id
 *   document   编辑器当前文档（含未保存修改，立即参与统计）
 *   onLocate(interval)  点击同讲区间时在顶部时间轴定位 {start,end}
 *
 * 统计在服务端完成：同一说话人的重叠片段先合并，再按全部起止时刻切分，
 * 单人区间计入独讲，多人区间给每人各计一次同讲；空说话人与零长度片段
 * 忽略，时间按毫秒计算、以秒显示。
 */
export default function SpeakingPanel({ projectId, document, onLocate }) {
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [activeKey, setActiveKey] = useState(null)

  // Recompute on every document change so unsaved segment edits show up
  // immediately; a short debounce coalesces rapid keystrokes into one call.
  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const data = await api.speakingTime(projectId, document)
        if (!cancelled) { setResult(data); setError('') }
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [projectId, document])

  if (error) {
    return (
      <div className="panel speaking-panel">
        <h3>发言时长</h3>
        <div className="error">{error}</div>
      </div>
    )
  }
  if (!result) {
    return (
      <div className="panel speaking-panel">
        <h3>发言时长</h3>
        <div className="muted small">统计中…</div>
      </div>
    )
  }

  const { speakers, overlapIntervals, allIntervals, summary } = result

  const locate = (ov) => {
    const key = `${ov.startMs}-${ov.endMs}`
    setActiveKey(key)
    onLocate({ start: ov.start, end: ov.end })
  }

  return (
    <div className="panel speaking-panel">
      <h3>发言时长</h3>
      <div className="muted small speaking-hint">
        依据转写片段的半开区间 [start, end)：先合并同一说话人的重叠片段，
        再按全部起止时刻切分。仅一人发言计为独讲；多人同讲的区间给每位
        说话人各计一次同讲时长。空说话人与零长度片段忽略，按毫秒统计、
        秒数显示。点击同讲区间可在时间轴定位。
      </div>

      {speakers.length === 0 && (
        <div className="empty-hint">暂无可统计的片段（需要非空说话人与非零时长）。</div>
      )}

      {speakers.length > 0 && (
        <>
          <div className="speaking-table-wrap">
            <table className="speaking-table">
              <thead>
                <tr>
                  <th>说话人</th>
                  <th className="num">独讲时长</th>
                  <th className="num">同讲时长</th>
                  <th className="num">合计发言</th>
                </tr>
              </thead>
              <tbody>
                {speakers.map((s) => (
                  <tr key={s.speaker}>
                    <td className="speaking-name">{s.speaker}</td>
                    <td className="num">{fmtSeconds(s.soloMs)}</td>
                    <td className="num">{fmtSeconds(s.overlapMs)}</td>
                    <td className="num strong">{fmtSeconds(s.spokenMs)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td>合计（{summary.speakers} 人）</td>
                  <td className="num">{fmtSeconds(summary.soloMs)}</td>
                  <td className="num" title="按说话人计数，一次同讲计给每位在场说话人">
                    {fmtSeconds(summary.overlapChargedMs)}
                  </td>
                  <td className="num" />
                </tr>
              </tfoot>
            </table>
          </div>
          <div className="muted small speaking-footnote">
            同讲时长合计按说话人计数（{fmtSeconds(summary.overlapChargedMs)}）；
            同讲区间覆盖的时钟时长为 {fmtSeconds(summary.overlapMs)}。
          </div>

          <div className="speaking-overlaps">
            <h4>
              同讲区间（{overlapIntervals.length}）
              {allIntervals.length > 0 && (
                <span className="tag speaking-all-tag">
                  全体同时发言 {allIntervals.length} 处
                </span>
              )}
            </h4>
            {overlapIntervals.length === 0 && (
              <div className="muted small speaking-empty">
                没有多人同时发言的区间。
              </div>
            )}
            <div className="speaking-overlap-list">
              {overlapIntervals.map((ov) => {
                const key = `${ov.startMs}-${ov.endMs}`
                return (
                  <div
                    key={key}
                    className={
                      `speaking-overlap${ov.all ? ' all' : ''}`
                      + `${activeKey === key ? ' active' : ''}`
                    }
                    onClick={() => locate(ov)}
                    title="点击在时间轴定位"
                  >
                    <div className="speaking-overlap-head">
                      <span className="speaking-overlap-range">
                        {fmtTime(ov.start)} – {fmtTime(ov.end)}
                      </span>
                      <span className="tag">{fmtSeconds(ov.durationMs)}</span>
                      <span className="tag">{ov.speakerCount} 人同讲</span>
                      {ov.all && <span className="tag tag-all">全体同时</span>}
                    </div>
                    <div className="speaking-overlap-speakers">
                      {ov.speakers.join('、')}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/** Format a millisecond integer duration as seconds (3 decimals, trailing
 * zeroes trimmed). */
export function fmtSeconds(ms) {
  return `${trimZeros((Math.round(ms) / 1000).toFixed(3))}s`
}

function trimZeros(text) {
  return text.replace(/\.?0+$/, '')
}
