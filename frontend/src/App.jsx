import { useState, useRef, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from './context/AuthContext.jsx'

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

function formatDateTime(iso) {
  if (!iso) return '—'
  // Normalize common backend formats for JS Date:
  //   Plivo:  "2026-04-22 11:01:15.405729+00:00"  (space separator, 6-digit micros, has tz)
  //   ours:   "2026-04-22T11:01:15.405729"         (naive UTC — no tz suffix)
  // Without a tz suffix, JS assumes LOCAL time, which is wrong since our backend
  // stores naive UTC via datetime.utcnow(). Append "Z" so it's treated as UTC.
  let s = iso
  if (typeof s === 'string') {
    s = s.replace(' ', 'T')                 // space → T
    s = s.replace(/(\.\d{3})\d+/, '$1')     // trim microseconds to millis
    if (!/([+-]\d{2}:?\d{2}|Z)$/.test(s)) {
      s += 'Z'                              // naive → explicit UTC
    }
  }
  const d = new Date(s)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
}

function formatDuration(sec) {
  if (!sec) return '—'
  const n = Number(sec)
  if (!n) return '—'
  const m = Math.floor(n / 60)
  const s = Math.floor(n % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function statusBadgeClass(status) {
  if (status === 'done') return 'step-badge done'
  if (status === 'error') return 'step-badge error'
  return 'step-badge active'
}

// Recording URLs come with our ngrok base (so Plivo can reach them), but the browser
// should fetch them via the Vite proxy to avoid ngrok's interstitial warning page.
// Rewrite any URL that points at our /api/plivo/audio endpoint to a same-origin path.
function localizeAudioUrl(url) {
  if (!url) return url
  const match = url.match(/\/api\/plivo\/audio\/[^?#]+/)
  return match ? match[0] : url
}

function FieldRow({ label, value }) {
  if (value === null || value === undefined || value === '' || (Array.isArray(value) && value.length === 0)) {
    return (
      <div className="field-row">
        <span className="field-label">{label}</span>
        <span className="field-value empty">—</span>
      </div>
    )
  }

  const display = Array.isArray(value)
    ? value.join(', ')
    : typeof value === 'boolean'
    ? value ? 'Yes ✓' : 'No'
    : String(value)

  let className = 'field-value'
  if (label === 'Hot Lead' && value === true) className += ' highlight'
  if (label === 'Sentiment' && value === 'positive') className += ' positive'
  if (label === 'Sentiment' && value === 'negative') className += ' negative'

  return (
    <div className="field-row">
      <span className="field-label">{label}</span>
      <span className={className}>{display}</span>
    </div>
  )
}

function PipelineSteps({ step }) {
  const steps = [
    { id: 0, label: 'Upload Audio' },
    { id: 1, label: 'Sarvam AI Transcription' },
    { id: 2, label: 'AI Summary' },
    { id: 3, label: 'ERP Ready' },
  ]

  return (
    <div className="pipeline-steps">
      {steps.map((s, i) => (
        <span key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className={`step-badge ${step > s.id ? 'done' : step === s.id ? 'active' : ''}`}>
            {step > s.id ? '✓ ' : ''}{s.label}
          </span>
          {i < steps.length - 1 && <span className="step-arrow">→</span>}
        </span>
      ))}
    </div>
  )
}

function SummaryResults({ transcript, transcriptEnglish, summary, activeTab, setActiveTab, copied, copyJson }) {
  if (!transcript && !summary) return null

  return (
    <div className="results-container">
      <div className="tab-bar">
        <button className={`tab-btn ${activeTab === 'summary' ? 'active' : ''}`} onClick={() => setActiveTab('summary')}>ERP Summary</button>
        <button className={`tab-btn ${activeTab === 'transcript' ? 'active' : ''}`} onClick={() => setActiveTab('transcript')}>Transcript</button>
        <button className={`tab-btn ${activeTab === 'json' ? 'active' : ''}`} onClick={() => setActiveTab('json')}>Raw JSON</button>
      </div>

      {activeTab === 'summary' && summary && (
        <div className="summary-card">
          {summary.call_summary && (
            <div className="call-summary-box">
              <div className="call-summary-label">CALL SUMMARY</div>
              <div className="call-summary-text">{summary.call_summary}</div>
            </div>
          )}

          <div className="badge-row">
            {summary.hot_lead && <span className="hot-lead-badge">🔥 Hot Lead</span>}
            {summary.call_sentiment && (
              <span className="step-badge" style={{
                background: summary.call_sentiment === 'positive' ? '#081208' : summary.call_sentiment === 'negative' ? '#150808' : '#111',
                color: summary.call_sentiment === 'positive' ? '#40c060' : summary.call_sentiment === 'negative' ? '#e05050' : '#888',
                borderColor: summary.call_sentiment === 'positive' ? '#40c06033' : summary.call_sentiment === 'negative' ? '#e0505033' : '#222',
              }}>
                {summary.call_sentiment === 'positive' ? '😊' : summary.call_sentiment === 'negative' ? '😟' : '😐'} {summary.call_sentiment}
              </span>
            )}
          </div>

          <div className="section-label">CUSTOMER DETAILS</div>
          <FieldRow label="Customer Name" value={summary.customer_name} />
          <FieldRow label="Company" value={summary.company_name} />
          <FieldRow label="Contact" value={summary.contact_number} />
          <FieldRow label="Location" value={summary.location} />
          <FieldRow label="Enquiry Type" value={summary.enquiry_type} />

          <div className="section-label">GENSET REQUIREMENTS</div>
          <FieldRow label="Capacity (kVA)" value={summary.genset_details?.capacity_kva} />
          <FieldRow label="Fuel Type" value={summary.genset_details?.fuel_type} />
          <FieldRow label="Brand Preference" value={summary.genset_details?.brand_preference} />
          <FieldRow label="Phase" value={summary.genset_details?.phase} />
          <FieldRow label="Usage" value={summary.genset_details?.usage} />

          <div className="section-label">DEAL DETAILS</div>
          <FieldRow label="Budget Range" value={summary.budget_range} />
          <FieldRow label="Timeline" value={summary.timeline} />
          <FieldRow label="Competitors" value={summary.competitor_mentions} />
          <FieldRow label="Key Requirements" value={summary.key_requirements} />
          <FieldRow label="Existing Genset" value={summary.existing_genset_info} />

          <div className="section-label">FOLLOW UP</div>
          <FieldRow label="Next Steps" value={summary.next_steps} />
          <FieldRow label="Sentiment" value={summary.call_sentiment} />
          <FieldRow label="Hot Lead" value={summary.hot_lead} />
        </div>
      )}

      {activeTab === 'transcript' && (
        <div className="transcript-box">
          {transcriptEnglish && (
            <>
              <h4 style={{margin: '0 0 8px 0', color: '#4f8cff'}}>English Translation</h4>
              <pre className="transcript-text">{transcriptEnglish}</pre>
              <h4 style={{margin: '16px 0 8px 0', color: '#999'}}>Original</h4>
            </>
          )}
          <pre className="transcript-text">{transcript}</pre>
        </div>
      )}

      {activeTab === 'json' && (
        <div className="json-box">
          <button className={`copy-btn ${copied ? 'copied' : ''}`} onClick={copyJson}>
            {copied ? '✓ Copied' : 'Copy JSON'}
          </button>
          <pre className="json-text">
            {summary ? JSON.stringify(summary, null, 2) : 'No structured data'}
          </pre>
        </div>
      )}
    </div>
  )
}

function UploadView() {
  const [file, setFile] = useState(null)
  const [language, setLanguage] = useState('mr-IN')
  const [status, setStatus] = useState('idle')
  const [step, setStep] = useState(0)
  const [transcript, setTranscript] = useState('')
  const [transcriptEnglish, setTranscriptEnglish] = useState('')
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('summary')
  const [copied, setCopied] = useState(false)
  const fileRef = useRef(null)

  const handleFile = (f) => {
    if (f) {
      setFile(f)
      setStatus('idle')
      setStep(0)
      setTranscript('')
      setSummary(null)
      setError('')
    }
  }

  const processCall = async () => {
    if (!file) return
    setStatus('transcribing')
    setStep(1)
    setError('')
    setTranscript('')
    setSummary(null)

    try {
      const formData = new FormData()
      formData.append('audio', file)
      formData.append('language', language)

      const resp = await fetch('/api/analyze', { method: 'POST', body: formData })
      if (!resp.ok) {
        const errData = await resp.json()
        throw new Error(errData.detail || `Server error: ${resp.status}`)
      }

      setStep(2)
      setStatus('summarizing')

      const data = await resp.json()
      setTranscript(data.transcript)
      setTranscriptEnglish(data.transcript_english || '')
      setSummary(data.summary)
      setStep(3)
      setStatus('done')
      setActiveTab('summary')
    } catch (err) {
      setError(err.message)
      setStatus('error')
    }
  }

  const copyJson = () => {
    if (summary) {
      navigator.clipboard.writeText(JSON.stringify(summary, null, 2))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  return (
    <>
      <PipelineSteps step={step} />

      <div
        className={`upload-zone ${file ? 'has-file' : ''}`}
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); e.stopPropagation() }}
        onDrop={(e) => { e.preventDefault(); e.stopPropagation(); handleFile(e.dataTransfer.files[0]) }}
      >
        <input
          ref={fileRef}
          type="file"
          accept="audio/*"
          style={{ display: 'none' }}
          onChange={(e) => handleFile(e.target.files[0])}
        />
        {file ? (
          <div className="file-info">
            <div className="file-info-left">
              <div className="file-icon">🎙</div>
              <div>
                <div className="file-name">{file.name}</div>
                <div className="file-size">{formatFileSize(file.size)}</div>
              </div>
            </div>
            <span style={{ fontSize: 12, color: '#555' }}>Click to change</span>
          </div>
        ) : (
          <div>
            <div className="upload-icon">🎙</div>
            <div className="upload-text">Drop your call recording here, or click to browse</div>
            <div className="upload-formats">AAC · MP3 · WAV · M4A · OGG</div>
          </div>
        )}
      </div>

      <div className="language-row">
        <label>Call Language:</label>
        <select className="language-select" value={language} onChange={(e) => setLanguage(e.target.value)}>
          <option value="mr-IN">Marathi (मराठी)</option>
          <option value="hi-IN">Hindi (हिन्दी)</option>
          <option value="en-IN">English (Indian)</option>
          <option value="gu-IN">Gujarati (ગુજરાતી)</option>
          <option value="ta-IN">Tamil (தமிழ்)</option>
          <option value="te-IN">Telugu (తెలుగు)</option>
          <option value="kn-IN">Kannada (ಕನ್ನಡ)</option>
          <option value="bn-IN">Bengali (বাংলা)</option>
        </select>
      </div>

      <button
        className="process-btn"
        onClick={processCall}
        disabled={!file || status === 'transcribing' || status === 'summarizing'}
      >
        {status === 'transcribing' ? '⏳ Transcribing with Sarvam AI...'
          : status === 'summarizing' ? '⏳ Generating summary with Gemini...'
          : 'Analyze Call Recording'}
      </button>

      {error && (
        <div className="error-box">
          <div className="error-text">{error}</div>
        </div>
      )}

      <SummaryResults
        transcript={transcript}
        transcriptEnglish={transcriptEnglish}
        summary={summary}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        copied={copied}
        copyJson={copyJson}
      />
    </>
  )
}

function DialView() {
  const [number, setNumber] = useState('')
  const [status, setStatus] = useState('idle') // idle | dialing | done | error
  const [lastResult, setLastResult] = useState(null)
  const [error, setError] = useState('')

  const dial = async () => {
    const n = number.trim()
    if (!n || status === 'dialing') return
    setStatus('dialing')
    setError('')
    try {
      const resp = await fetch('/api/plivo/dial', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ number: n }),
      })
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}))
        throw new Error(errData.detail || `HTTP ${resp.status}`)
      }
      
      const data = await resp.json()
      setLastResult(data)
      setStatus('done')
    } catch (e) {
      setError(e.message)
      setStatus('error')
    }
  }

  return (
    <div className="dial-card">
      <div className="dial-title">📞 Outbound Call</div>
      <div className="dial-subtitle">
        Dial a customer's number. They'll hear our IVR menu — press 1 to talk to a salesperson, press 2 for the AI assistant.
        The call is recorded, transcribed, and summarized automatically.
      </div>

      <div className="dial-row">
        <input
          type="tel"
          className="dial-input"
          placeholder="+91 95455 56151  or  9545556151"
          value={number}
          onChange={e => setNumber(e.target.value)}
          disabled={status === 'dialing'}
          onKeyDown={e => { if (e.key === 'Enter') dial() }}
        />
        <button
          className="process-btn"
          style={{ width: 'auto', margin: 0, minWidth: 120 }}
          onClick={dial}
          disabled={!number.trim() || status === 'dialing'}
        >
          {status === 'dialing' ? '⏳ Dialing…' : '📞 Dial'}
        </button>
      </div>
      <div className="dial-hint">
        Indian numbers without country code work — we add +91 automatically. Bare 10-digit numbers, "0" prefixes, and spaces are all accepted.
      </div>

      {error && (
        <div className="error-box" style={{ marginTop: 16 }}>
          <div className="error-text">{error}</div>
        </div>
      )}

      {lastResult && status === 'done' && (
        <div className="dial-result">
          <div className="dial-result-label">CALL QUEUED</div>
          <div className="dial-result-row"><span>To:</span> <strong>{lastResult.to}</strong></div>
          <div className="dial-result-row"><span>From:</span> <strong>{lastResult.from}</strong></div>
          <div className="dial-result-row" style={{ fontSize: 11, color: '#666' }}>
            <span>Plivo request:</span> <code>{lastResult.request_uuid}</code>
          </div>
          <div className="dial-result-note">
            The number is ringing now. Once the call ends, it'll appear in the <strong>Recorded Calls</strong> tab with transcript and summary.
          </div>
        </div>
      )}
    </div>
  )
}


function CallsView() {
  const [calls, setCalls] = useState([])
  const [recordings, setRecordings] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [activeTab, setActiveTab] = useState('summary')
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [processingUrl, setProcessingUrl] = useState(null)

  const fetchCalls = async () => {
    try {
      const [callsResp, recResp] = await Promise.all([
        fetch('/api/plivo/calls'),
        fetch('/api/plivo/recordings'),
      ])
      if (!callsResp.ok) throw new Error(`calls: HTTP ${callsResp.status}`)
      const callsData = await callsResp.json()
      setCalls(callsData.calls || [])
      if (recResp.ok) {
        const recData = await recResp.json()
        setRecordings(recData.recordings || [])
      }
      setError('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchCalls()
    const t = setInterval(fetchCalls, 5000)
    return () => clearInterval(t)
  }, [])

  const processRecording = async (rec) => {
    setProcessingUrl(rec.recording_url)
    try {
      const resp = await fetch('/api/plivo/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recording_url: rec.recording_url,
          from_number: rec.from_number,
          to_number: rec.to_number,
        }),
      })
      if (!resp.ok) {
        const errData = await resp.json()
        throw new Error(errData.detail || `HTTP ${resp.status}`)
      }
      const data = await resp.json()
      setSelectedId(data.call_id)
      await fetchCalls()
    } catch (e) {
      setError(`Processing failed: ${e.message}`)
    } finally {
      setProcessingUrl(null)
    }
  }

  const retryRecording = async (callId) => {
    setProcessingUrl(callId)
    try {
      const resp = await fetch(`/api/plivo/calls/${callId}/retry`, { method: 'POST' })
      if (!resp.ok) {
        const errData = await resp.json()
        throw new Error(errData.detail || `HTTP ${resp.status}`)
      }
      await fetchCalls()
    } catch (e) {
      setError(`Retry failed: ${e.message}`)
    } finally {
      setProcessingUrl(null)
    }
  }

  // Build the merged list:
  //   1. Every processed call (from /api/plivo/calls) — these are completed Q/A conversations OR
  //      manually-imported recordings. They have transcripts and summaries.
  //   2. For raw Plivo recordings, we SKIP any whose call_uuid matches an already-processed call
  //      (those short clips are per-turn pieces of a completed conversation). What's left is
  //      recordings from attempts that never completed — we group by call_uuid so a single
  //      attempted-but-incomplete call still shows as one item.
  const processedCallUuids = new Set(calls.map(c => c.call_uuid).filter(Boolean))
  const processedByUrl = new Map(calls.filter(c => c.recording_url).map(c => [c.recording_url, c]))

  const unmatchedRecordings = recordings.filter(r =>
    !processedCallUuids.has(r.call_uuid) && !processedByUrl.has(r.recording_url)
  )

  // Group unmatched recordings by call_uuid so we show one item per attempted call.
  const recsByCallUuid = new Map()
  for (const rec of unmatchedRecordings) {
    const key = rec.call_uuid || `orphan:${rec.recording_id}`
    if (!recsByCallUuid.has(key)) recsByCallUuid.set(key, [])
    recsByCallUuid.get(key).push(rec)
  }

  const mergedItems = [
    ...calls.map(c => ({ ...c, kind: 'processed' })),
    ...Array.from(recsByCallUuid.entries()).map(([callUuid, recs]) => {
      // Pick the longest recording from the group as the "primary" for playback
      const primary = recs.slice().sort((a, b) =>
        (b.recording_duration_ms || 0) - (a.recording_duration_ms || 0)
      )[0]
      const totalMs = recs.reduce((n, r) => n + (r.recording_duration_ms || 0), 0)
      return {
        kind: 'recording',
        id: `rec:${callUuid}`,
        call_uuid: callUuid,
        recording_url: primary.recording_url,
        from_number: primary.from_number,
        to_number: primary.to_number,
        started_at: primary.add_time,
        duration: totalMs ? Math.round(totalMs / 1000) : null,
        status: 'not_processed',
        summary: null,
        transcript: null,
        transcript_english: null,
        error: null,
        _raw: primary,
        _all_recs: recs,
      }
    }),
  ]

  const selected = mergedItems.find(it => it.id === selectedId)

  const copyJson = () => {
    if (selected?.summary) {
      navigator.clipboard.writeText(JSON.stringify(selected.summary, null, 2))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  return (
    <>
      <div className="calls-header">
        <div>
          <div className="calls-title">Recorded Phone Calls</div>
          <div className="calls-subtitle">
            Pulled live from your Plivo account. Click a recording to transcribe + summarize.
          </div>
        </div>
        <button className="refresh-btn" onClick={fetchCalls}>⟳ Refresh</button>
      </div>

      {error && (
        <div className="error-box">
          <div className="error-text">Error loading calls: {error}</div>
        </div>
      )}

      {loading && mergedItems.length === 0 && (
        <div className="empty-state">Loading…</div>
      )}

      {!loading && mergedItems.length === 0 && !error && (
        <div className="empty-state">
          <div style={{ fontSize: 28, marginBottom: 8 }}>📞</div>
          <div>No Plivo recordings found on your account.</div>
          <div style={{ fontSize: 12, color: '#555', marginTop: 6 }}>
            Call your Plivo number to create one. Recordings appear here automatically.
          </div>
        </div>
      )}

      {mergedItems.length > 0 && (
        <div className="calls-layout">
          <div className="calls-list">
            {mergedItems.map(item => {
              const isProcessing = processingUrl === item.recording_url
              return (
                <button
                  key={item.id}
                  className={`call-item ${selectedId === item.id ? 'selected' : ''}`}
                  onClick={() => { setSelectedId(item.id); setActiveTab('summary') }}
                >
                  <div className="call-item-top">
                    <span className="call-from">{item.from_number || 'Unknown'}</span>
                    <span
                      className={statusBadgeClass(item.status)}
                      style={{ fontSize: 10, padding: '2px 8px' }}
                    >
                      {item.status === 'not_processed' ? 'not processed' : item.status}
                    </span>
                  </div>
                  <div className="call-item-meta">
                    <span>{formatDateTime(item.started_at)}</span>
                    {item.duration && <span> · {formatDuration(item.duration)}</span>}
                  </div>
                  {item.agent_name && (
                    <div className="call-item-meta" style={{ color: '#666' }}>→ {item.agent_name}</div>
                  )}
                  {item.summary?.call_summary && (
                    <div className="call-item-preview">{item.summary.call_summary}</div>
                  )}
                  {item.error && (
                    <div className="call-item-preview" style={{ color: '#e05050' }}>{item.error}</div>
                  )}
                  {item.kind === 'recording' && (
                    <button
                      className="inline-process-btn"
                      onClick={(e) => { e.stopPropagation(); processRecording(item._raw) }}
                      disabled={isProcessing}
                    >
                      {isProcessing ? 'Queuing…' : '▸ Transcribe & Summarize'}
                    </button>
                  )}
                  {item.kind === 'processed' && item.status === 'awaiting_recording' && (
                    <button
                      className="inline-process-btn"
                      onClick={(e) => { e.stopPropagation(); retryRecording(item.id) }}
                      disabled={processingUrl === item.id}
                    >
                      {processingUrl === item.id ? 'Retrying…' : '↻ Retry fetch recording'}
                    </button>
                  )}
                </button>
              )
            })}
          </div>

          <div className="calls-detail">
            {!selected && (
              <div className="empty-state" style={{ border: '1px dashed #1a1a1a' }}>
                Select a call on the left to view its transcript and summary.
              </div>
            )}

            {selected && (
              <>
                <div className="call-detail-header">
                  <div>
                    <div className="call-detail-title">
                      From {selected.from_number || 'Unknown'} → {selected.to_number || '—'}
                    </div>
                    <div className="call-detail-meta">
                      {formatDateTime(selected.started_at)}
                      {selected.duration && ` · ${formatDuration(selected.duration)}`}
                      {selected.agent_name && ` · Agent: ${selected.agent_name}`}
                    </div>
                  </div>
                  <span className={statusBadgeClass(selected.status)}>{selected.status}</span>
                </div>

                {selected.recording_url && (
                  <div className="audio-box">
                    <div className="audio-label">RECORDING</div>
                    <audio controls src={localizeAudioUrl(selected.recording_url)} style={{ width: '100%' }} />
                    <a className="recording-link" href={localizeAudioUrl(selected.recording_url)} target="_blank" rel="noreferrer">
                      Open in new tab
                    </a>
                  </div>
                )}

                {selected.error && (
                  <div className="error-box">
                    <div className="error-text">Processing error: {selected.error}</div>
                  </div>
                )}

                {selected.status !== 'done' && !selected.summary && (
                  <div className="empty-state">
                    <div style={{ fontSize: 24, marginBottom: 8 }}>⏳</div>
                    <div>Processing: {selected.status}…</div>
                    <div style={{ fontSize: 12, color: '#555', marginTop: 6 }}>
                      This page auto-refreshes every 5 seconds.
                    </div>
                  </div>
                )}

                <SummaryResults
                  transcript={selected.transcript || ''}
                  transcriptEnglish={selected.transcript_english || ''}
                  summary={selected.summary}
                  activeTab={activeTab}
                  setActiveTab={setActiveTab}
                  copied={copied}
                  copyJson={copyJson}
                />
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}

function CallCustomersView() {
  const [enquiries, setEnquiries] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [month, setMonth] = useState(() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  })
  const [filter, setFilter] = useState('')
  // call status per enquiry_no: 'idle' | 'calling' | 'in_progress' | 'error'
  const [callState, setCallState] = useState({})
  // erpHealth: null while loading, then { status: 'ok' } or { status: 'error', error }
  const [erpHealth, setErpHealth] = useState(null)

  const fetchEnquiries = async (m) => {
    setLoading(true)
    try {
      const r = await fetch(`/api/erp/enquiries?month=${encodeURIComponent(m)}`)
      if (!r.ok) {
        const txt = await r.text()
        throw new Error(txt || `HTTP ${r.status}`)
      }
      const data = await r.json()
      setEnquiries(data.enquiries || [])
      setError('')
    } catch (e) {
      setError(e.message)
      setEnquiries([])
    } finally {
      setLoading(false)
    }
  }

  const checkErp = async () => {
    try {
      const r = await fetch('/api/erp/health')
      setErpHealth(await r.json())
    } catch (e) {
      setErpHealth({ status: 'error', error: e.message })
    }
  }

  useEffect(() => {
    checkErp()
    fetchEnquiries(month)
  }, [month])

  const placeCall = async (enq) => {
    if (!enq.callable_phone) return
    setCallState(s => ({ ...s, [enq.enquiry_no]: 'calling' }))
    try {
      const r = await fetch('/api/agent/call', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: enq.callable_phone,
          customer_name: enq.customer_name,
          enquiry_no: enq.enquiry_no,
        }),
      })
      if (!r.ok) {
        const data = await r.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${r.status}`)
      }
      setCallState(s => ({ ...s, [enq.enquiry_no]: 'in_progress' }))
    } catch (e) {
      setCallState(s => ({ ...s, [enq.enquiry_no]: 'error' }))
      setError(`Call failed for ${enq.enquiry_no}: ${e.message}`)
    }
  }

  const filtered = filter
    ? enquiries.filter(e =>
        (e.customer_name || '').toLowerCase().includes(filter.toLowerCase())
        || (e.contact_person || '').toLowerCase().includes(filter.toLowerCase())
        || (e.mobile || '').includes(filter)
        || (e.enquiry_no || '').toLowerCase().includes(filter.toLowerCase())
      )
    : enquiries

  return (
    <>
      <div className="cc-toolbar">
        <div className="api-status-item">
          <div className={`api-dot ${erpHealth?.status === 'ok' ? 'ok' : 'missing'}`} />
          ERP DB {erpHealth?.status === 'ok' ? 'Connected' : (erpHealth?.status === 'error' ? 'Offline' : '…')}
        </div>
        <label className="cc-month-label">
          Month:
          <input
            type="month"
            value={month}
            onChange={e => setMonth(e.target.value)}
            className="cc-input cc-input-month"
          />
        </label>
        <input
          type="text"
          placeholder="Search name / phone / EnqNo…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="cc-input cc-input-search"
        />
        <button onClick={() => fetchEnquiries(month)} className="refresh-btn">
          ⟳ Refresh
        </button>
        <span className="cc-count">
          {filtered.length} of {enquiries.length} enquiries
        </span>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {loading && enquiries.length === 0 && <div className="empty-state">Loading enquiries…</div>}
      {!loading && enquiries.length === 0 && !error && (
        <div className="empty-state">No enquiries found for {month}.</div>
      )}

      {filtered.length > 0 && (
        <div className="cc-table-wrap">
          <table className="cc-table">
            <thead>
              <tr>
                <th>EnqNo</th>
                <th>Date</th>
                <th>Customer</th>
                <th>Contact</th>
                <th>Mobile</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(enq => {
                const state = callState[enq.enquiry_no] || 'idle'
                const btnLabel = {
                  idle: '📞 Call',
                  calling: 'Initiating…',
                  in_progress: '✓ Call placed',
                  error: 'Retry',
                }[state]
                const btnDisabled = state === 'calling' || state === 'in_progress' || !enq.callable_phone
                return (
                  <tr key={enq.enquiry_no}>
                    <td className="cc-cell-enq">{enq.enquiry_no}</td>
                    <td className="cc-cell-muted">
                      {enq.enquiry_date ? formatDateTime(enq.enquiry_date) : '—'}
                    </td>
                    <td className="cc-cell-strong">{enq.customer_name || '—'}</td>
                    <td className="cc-cell-muted">{enq.contact_person || '—'}</td>
                    <td className="cc-cell-mono">
                      {enq.callable_phone || (
                        <span className="cc-cell-unusable">
                          {enq.mobile ? `${enq.mobile} (unusable)` : '—'}
                        </span>
                      )}
                    </td>
                    <td>
                      <button
                        onClick={() => placeCall(enq)}
                        disabled={btnDisabled}
                        className={`cc-call-btn cc-call-btn-${state}`}
                      >
                        {btnLabel}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function SummaryView() {
  const [leads, setLeads] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchLeads = async () => {
    try {
      const r = await fetch('/api/agent/leads')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      setLeads(data.leads || [])
      setError('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchLeads()
    const t = setInterval(fetchLeads, 5000)
    return () => clearInterval(t)
  }, [])

  const selected = leads.find(l => l.id === selectedId)

  return (
    <>
      {error && <div className="error-banner">{error}</div>}
      {loading && leads.length === 0 && <div className="empty-state">Loading…</div>}
      {!loading && leads.length === 0 && (
        <div className="empty-state">
          No call summaries yet. After the AI agent completes a call with a
          customer (inbound or outbound from the Call Customers tab), the
          summary will appear here.
        </div>
      )}

      {leads.length > 0 && (
        <div className="calls-layout">
          <div className="calls-list">
            <div className="calls-list-header">
              Summaries ({leads.length})
            </div>
            {leads.map(lead => {
              const f = lead.fields || {}
              const name = f.customer_name || '(no name captured)'
              // Show customer phone: prefer the explicit callback the agent
              // captured; fall back to caller-ID (inbound) or to_number (outbound).
              const phone = f.callback_number || lead.from_number || lead.to_number || ''
              return (
                <button
                  key={lead.id}
                  className={`call-list-item ${selectedId === lead.id ? 'active' : ''}`}
                  onClick={() => setSelectedId(lead.id)}
                >
                  <div className="call-list-item-top">
                    <span style={{ fontWeight: 600 }}>{name}</span>
                  </div>
                  <div className="call-list-item-sub" style={{ fontFamily: 'monospace' }}>
                    {phone || '—'}
                  </div>
                </button>
              )
            })}
          </div>

          <div className="call-detail">
            {!selected && <div className="empty-state">Select a lead to view details</div>}
            {selected && (
              <>
                <div className="call-detail-header">
                  <div>
                    <div className="call-detail-title">
                      {selected.fields?.customer_name || '(no name)'}
                    </div>
                    <div className="call-detail-sub">
                      {[
                        selected.fields?.designation,
                        selected.fields?.company_name,
                      ].filter(Boolean).join(' · ') || '—'}
                    </div>
                    <div className="call-detail-meta">
                      Captured {formatDateTime(selected.captured_at)}
                      {selected.from_number && ` · from ${selected.from_number}`}
                      {selected.to_number && ` · to ${selected.to_number}`}
                    </div>
                  </div>
                </div>

                <div className="summary-card" style={{ marginTop: 16 }}>
                  {selected.fields?.call_summary && (
                    <div className="call-summary-box">
                      <div className="call-summary-label">CALL SUMMARY</div>
                      <div className="call-summary-text">{selected.fields.call_summary}</div>
                    </div>
                  )}

                  <div className="badge-row">
                    {selected.fields?.hot_lead && <span className="hot-lead-badge">🔥 Hot Lead</span>}
                    {selected.fields?.language_used && (
                      <span className="step-badge">{selected.fields.language_used}</span>
                    )}
                  </div>

                  <div className="section-label">CUSTOMER</div>
                  <FieldRow label="Name" value={selected.fields?.customer_name} />
                  <FieldRow label="Designation" value={selected.fields?.designation} />
                  <FieldRow label="Company" value={selected.fields?.company_name} />
                  <FieldRow label="Business Type" value={selected.fields?.business_type} />
                  <FieldRow label="Location" value={selected.fields?.location} />

                  <div className="section-label">REQUIREMENT</div>
                  <FieldRow label="Purpose" value={selected.fields?.purpose} />
                  <FieldRow label="New / Replacement" value={selected.fields?.new_or_replacement} />
                  <FieldRow label="Capacity (kVA)" value={selected.fields?.capacity_kva} />
                  <FieldRow label="Load Calculated" value={selected.fields?.load_calculated} />
                  <FieldRow label="Phase" value={selected.fields?.phase} />
                  <FieldRow label="Fuel Type" value={selected.fields?.fuel_type} />
                  <FieldRow label="AMF Required" value={selected.fields?.amf_required} />
                  <FieldRow label="Canopy" value={selected.fields?.canopy_required} />

                  <div className="section-label">COMMERCIAL</div>
                  <FieldRow label="Timeline" value={selected.fields?.timeline} />
                  <FieldRow label="Budget" value={selected.fields?.budget_range} />
                  <FieldRow label="Site Visit OK" value={selected.fields?.site_visit_ok} />
                  <FieldRow label="Got Other Quotes" value={selected.fields?.competitor_quotes_received} />
                  <FieldRow label="Competitor Brands" value={selected.fields?.competitor_brands} />

                  <div className="section-label">FOLLOW-UP CONTACT</div>
                  <FieldRow label="Callback Number" value={selected.fields?.callback_number} />
                  <FieldRow label="Email" value={selected.fields?.email} />
                  <FieldRow label="Other Questions" value={selected.fields?.other_questions} />
                </div>

                <details style={{ marginTop: 16 }}>
                  <summary style={{ cursor: 'pointer', color: '#888', fontSize: 12 }}>
                    Raw JSON
                  </summary>
                  <pre className="json-text" style={{ marginTop: 8 }}>
                    {JSON.stringify(selected, null, 2)}
                  </pre>
                </details>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}

export default function App() {
  const [apiHealth, setApiHealth] = useState(null)
  const [mode, setMode] = useState('call_customers') // 'call_customers' | 'summary' | 'upload' | 'calls' | 'dial'

  // Federation auth handoff (federation-cookbook §4.5.1 + auth-contract §3).
  // When the shell mounts this remote it navigates with `?email=...&autoLogin=true`.
  // In standalone dev these params are absent and `user` stays null — that's fine,
  // nothing in this app gates on auth today.
  const [searchParams] = useSearchParams()
  const { setUser } = useAuth()
  useEffect(() => {
    if (searchParams.get('autoLogin') === 'true') {
      setUser({
        email: searchParams.get('email'),
        displayName: searchParams.get('displayName'),
        role: searchParams.get('role'),
        appAdmin: searchParams.get('appAdmin') === 'true',
        adminScope: (searchParams.get('adminScope') || '').split(',').filter(Boolean),
      })
    }
  }, [searchParams, setUser])

  useEffect(() => {
    const check = () => {
      fetch('/api/health')
        .then(r => r.json())
        .then(data => setApiHealth(data))
        .catch(() => setApiHealth({ status: 'offline', sarvam_configured: false, openrouter_configured: false }))
    }
    check()
    const t = setInterval(check, 15000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="app-container">
      <header className="header">
        <div className="header-left">
          <div className="header-dot" />
          <span className="header-title">Genset Call Analyzer</span>
          <span className="header-subtitle">Marathi → ERP</span>
        </div>
      </header>

      <div className="main-content">
        <div className="api-status-bar">
          <div className="api-status-item">
            <div className={`api-dot ${apiHealth?.status === 'ok' ? 'ok' : 'missing'}`} />
            Backend {apiHealth?.status === 'ok' ? 'Connected' : 'Offline'}
          </div>
          <div className="api-status-item">
            <div className={`api-dot ${apiHealth?.sarvam_configured ? 'ok' : 'missing'}`} />
            Sarvam API {apiHealth?.sarvam_configured ? 'Ready' : 'Key Missing'}
          </div>
          <div className="api-status-item">
            <div className={`api-dot ${apiHealth?.openrouter_configured ? 'ok' : 'missing'}`} />
            OpenRouter API {apiHealth?.openrouter_configured ? 'Ready' : 'Key Missing'}
          </div>
          <div className="api-status-item">
            <div className={`api-dot ${apiHealth?.plivo_configured ? 'ok' : 'missing'}`} />
            Plivo {apiHealth?.plivo_configured ? 'Ready' : 'Not configured'}
          </div>
        </div>

        <div className="mode-tabs">
          <button className={`mode-tab ${mode === 'call_customers' ? 'active' : ''}`} onClick={() => setMode('call_customers')}>
            Call Customers (ERP)
          </button>
          <button className={`mode-tab ${mode === 'summary' ? 'active' : ''}`} onClick={() => setMode('summary')}>
            Summary
          </button>
          <button className={`mode-tab ${mode === 'upload' ? 'active' : ''}`} onClick={() => setMode('upload')}>
            Upload Recording
          </button>
          <button className={`mode-tab ${mode === 'calls' ? 'active' : ''}`} onClick={() => setMode('calls')}>
            Recorded Calls
          </button>
          <button className={`mode-tab ${mode === 'dial' ? 'active' : ''}`} onClick={() => setMode('dial')}>
            Dial
          </button>
        </div>

        {mode === 'call_customers' && <CallCustomersView />}
        {mode === 'summary' && <SummaryView />}
        {mode === 'upload' && <UploadView />}
        {mode === 'calls' && <CallsView />}
        {mode === 'dial' && <DialView />}
      </div>
    </div>
  )
}
