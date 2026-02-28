'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { usePipelineStore, UserStory, WorkflowStatus, ConflictEntry, JiraResult } from '@/store/pipelineStore'
import { WizardStepper } from '@/components/WizardStepper'
import { PRDViewer } from '@/components/PRDViewer'
import { StoriesViewer } from '@/components/StoriesViewer'
import { OpenQuestionsPanel } from '@/components/OpenQuestionsPanel'
import { useFileUpload } from '@/hooks/useFileUpload'
import { useSSEStream } from '@/hooks/useSSEStream'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function statusToStep(status: WorkflowStatus | null): string {
  switch (status) {
    case null:
    case 'TRANSCRIPT_UPLOADED':
      return 'upload'
    case 'PRD_GENERATED':
      return 'review'
    case 'PRD_APPROVED':
      return 'stories'
    case 'STORIES_GENERATED':
    case 'JIRA_PUSH_PENDING':
      return 'jira'
    case 'JIRA_PUSH_SUCCESS':
    case 'COMPLETED':
      return 'jira'
    default:
      return 'upload'
  }
}

export default function WorkflowPage() {
  const router = useRouter()
  const store = usePipelineStore()
  const { upload, uploading, uploadAdditional, addingTranscripts, error: uploadError } = useFileUpload()

  const [file, setFile] = useState<File | null>(null)
  const [additionalFiles, setAdditionalFiles] = useState<File[]>([])
  const [generatingPRD, setGeneratingPRD] = useState(false)
  const [generatingStories, setGeneratingStories] = useState(false)
  const [storyStepLabel, setStoryStepLabel] = useState('')
  const [pushingJira, setPushingJira] = useState(false)
  const [jiraError, setJiraError] = useState<string | null>(null)
  const [sseError, setSSEError] = useState<string | null>(null)
  const [savingAnswers, setSavingAnswers] = useState(false)
  const [answersSaved, setAnswersSaved] = useState(false)
  const didRehydrate = useRef(false)

  const currentStep = statusToStep(store.status)

  // Rehydrate from server on mount if we have a saved projectId
  useEffect(() => {
    if (didRehydrate.current) return
    if (!store.projectId && !store.projectName) {
      router.replace('/')
      return
    }
    didRehydrate.current = true
    if (store.projectId) {
      fetch(`${API}/projects/${store.projectId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data) store.rehydrateFromServer(data)
        })
        .catch(() => {})
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // PRD SSE handler
  const handlePRDEvent = useCallback(
    (event: Record<string, unknown>) => {
      if (event.section && event.data !== undefined) {
        store.upsertPRDSection(event.section as string, event.data)
      }
      if (event.hallucination_count !== undefined) {
        store.setHallucinations(event.hallucination_count as number)
      }
      if (event.done) {
        setGeneratingPRD(false)
        store.setStatus('PRD_GENERATED')
      }
    },
    [store]
  )

  // Story SSE handler
  const handleStoryEvent = useCallback(
    (event: Record<string, unknown>) => {
      if (event.step === 'extracting_capabilities') setStoryStepLabel('Extracting capabilities…')
      if (event.step === 'slices_planned') setStoryStepLabel(`Planning ${event.count} feature slices…`)
      if (event.step === 'story_done' && event.story) {
        store.addStory(event.story as UserStory)
        setStoryStepLabel('Generating stories…')
      }
      if (event.step === 'story_diff' && event.classification === 'obsolete') {
        // obsolete stories are soft-deleted server-side; remove from client list
        store.removeStoryById(event.story_id as string)
      }
      if (event.done) {
        setGeneratingStories(false)
        setStoryStepLabel('')
        store.setStatus('STORIES_GENERATED')
      }
    },
    [store]
  )

  const { stream: streamPRD } = useSSEStream({
    onEvent: handlePRDEvent,
    onError: (e) => { setSSEError(e.message); setGeneratingPRD(false) },
    onDone: () => setGeneratingPRD(false),
  })

  const { stream: streamStories } = useSSEStream({
    onEvent: handleStoryEvent,
    onError: (e) => { setSSEError(e.message); setGeneratingStories(false) },
    onDone: () => setGeneratingStories(false),
  })

  // Upload transcript
  const handleUpload = async () => {
    if (!file || !store.projectName) return
    const result = await upload(file, store.projectName)
    if (result) {
      store.setProjectId(result.project_id)
      store.setStatus('TRANSCRIPT_UPLOADED')
    }
  }

  // Add more transcripts (after initial upload)
  const handleAddTranscripts = async () => {
    if (!additionalFiles.length || !store.projectId) return
    const result = await uploadAdditional(additionalFiles, store.projectId)
    if (result) {
      setAdditionalFiles([])
    }
  }

  // Generate PRD (first gen or re-gen with answers)
  const handleGeneratePRD = async () => {
    if (!store.projectId) return
    setGeneratingPRD(true)
    setSSEError(null)
    setAnswersSaved(false)
    await streamPRD(`${API}/generate/prd`, { project_id: store.projectId })
  }

  // Approve PRD
  const handleApprovePRD = async () => {
    if (!store.projectId) return
    setSSEError(null)
    const res = await fetch(`${API}/projects/${store.projectId}/approve`, { method: 'POST' })
    if (res.ok) store.setStatus('PRD_APPROVED')
  }

  // Generate Stories
  const handleGenerateStories = async () => {
    if (!store.projectId) return
    setGeneratingStories(true)
    setSSEError(null)
    await streamStories(`${API}/generate/stories`, { project_id: store.projectId })
  }

  // Push to Jira
  const handlePushJira = async () => {
    if (!store.projectId) return
    setPushingJira(true)
    setJiraError(null)
    try {
      const res = await fetch(`${API}/jira/tickets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: store.projectId }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data: JiraResult & { issue_keys?: string[] } = await res.json()
      store.setJiraResult({
        created: data.created ?? [],
        updated: data.updated ?? [],
        deleted: data.deleted ?? [],
        count: data.count ?? 0,
      })
      store.setStatus('JIRA_PUSH_SUCCESS')
    } catch (err: unknown) {
      setJiraError(err instanceof Error ? err.message : String(err))
    } finally {
      setPushingJira(false)
    }
  }

  // Save open question answers
  const handleSaveAnswers = async () => {
    if (!store.projectId) return
    setSavingAnswers(true)
    setAnswersSaved(false)
    try {
      const res = await fetch(`${API}/projects/${store.projectId}/qa-answers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(store.gapAnswers),
      })
      if (res.ok) setAnswersSaved(true)
    } finally {
      setSavingAnswers(false)
    }
  }

  // Export DOCX
  const handleExport = () => {
    if (!store.projectId) return
    window.open(`${API}/export/${store.projectId}/docx`, '_blank')
  }

  const openQuestions = store.prdSections.open_questions
  const conflicts = openQuestions?.type1_conflicts ?? []
  const gaps = openQuestions?.type2_gaps ?? []

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 tracking-tight">{store.projectName || 'DocHub'}</h1>
            <p className="text-sm text-gray-400 mt-0.5">AI-powered product requirements pipeline</p>
          </div>
          <button
            onClick={() => { store.reset(); router.replace('/') }}
            className="flex items-center gap-1.5 text-xs font-medium text-gray-400 hover:text-indigo-600 border border-gray-200 hover:border-indigo-300 rounded-lg px-3 py-1.5 transition-colors"
            title="The knowledge base is shared across projects. Click to start a new project."
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New Project
          </button>
        </div>

        <WizardStepper currentStep={currentStep} />

        {/* Error banner */}
        {(sseError || uploadError || jiraError) && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-4 flex items-start gap-2">
            <span className="text-red-500 mt-0.5 flex-shrink-0">⚠</span>
            <p className="text-sm text-red-700 font-medium">{sseError || uploadError || jiraError}</p>
          </div>
        )}

        {/* Step: Upload */}
        {(currentStep === 'upload') && (
          <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-6 space-y-4">
            <div>
              <h2 className="font-semibold text-gray-900 text-base">Upload Meeting Transcript</h2>
              <p className="text-sm text-gray-500 mt-0.5">Accepts .txt or .docx files</p>
            </div>

            <div
              className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center cursor-pointer hover:border-blue-400 transition-colors"
              onClick={() => document.getElementById('file-input')?.click()}
            >
              <div className="text-3xl mb-2">📁</div>
              <p className="text-sm text-gray-600">
                {file ? file.name : 'Click to select a transcript file'}
              </p>
              <input
                id="file-input"
                type="file"
                accept=".txt,.docx"
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>

            <button
              onClick={handleUpload}
              disabled={!file || uploading}
              className="w-full bg-blue-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {uploading ? 'Uploading…' : 'Upload & Continue'}
            </button>

            {/* After initial upload: add more transcripts */}
            {store.status === 'TRANSCRIPT_UPLOADED' && (
              <div className="space-y-3 border-t border-gray-100 pt-4">
                <p className="text-sm font-medium text-gray-700">
                  Add more transcripts (optional)
                </p>
                <p className="text-xs text-gray-500">
                  Upload additional meeting files — they will be merged with the first transcript before PRD generation.
                </p>

                <div
                  className="border-2 border-dashed border-gray-200 rounded-lg p-4 text-center cursor-pointer hover:border-indigo-300 transition-colors"
                  onClick={() => document.getElementById('additional-file-input')?.click()}
                >
                  <p className="text-sm text-gray-500">
                    {additionalFiles.length > 0
                      ? additionalFiles.map((f) => f.name).join(', ')
                      : 'Click to select additional transcripts'}
                  </p>
                  <input
                    id="additional-file-input"
                    type="file"
                    accept=".txt,.docx"
                    multiple
                    className="hidden"
                    onChange={(e) => setAdditionalFiles(Array.from(e.target.files ?? []))}
                  />
                </div>

                {additionalFiles.length > 0 && (
                  <button
                    onClick={handleAddTranscripts}
                    disabled={addingTranscripts}
                    className="w-full bg-indigo-50 border border-indigo-300 text-indigo-700 rounded-lg py-2 text-sm font-semibold hover:bg-indigo-100 disabled:opacity-40 transition-colors"
                  >
                    {addingTranscripts
                      ? 'Adding…'
                      : `Add ${additionalFiles.length} transcript${additionalFiles.length > 1 ? 's' : ''}`}
                  </button>
                )}

                <button
                  onClick={handleGeneratePRD}
                  disabled={generatingPRD}
                  className="w-full bg-indigo-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-indigo-700 disabled:opacity-40 transition-colors"
                >
                  {generatingPRD ? 'Generating PRD…' : 'Generate PRD →'}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Step: PRD review */}
        {(currentStep === 'review' || currentStep === 'prd') && (
          <div className="space-y-4">
            {/* PRD sections (title → audience) */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold text-gray-800">Product Requirements Document</h2>
                <div className="flex gap-2">
                  <button
                    onClick={handleExport}
                    className="text-xs border border-gray-300 rounded px-3 py-1.5 hover:bg-gray-50"
                  >
                    Export DOCX
                  </button>
                  {!generatingPRD && store.status === 'PRD_GENERATED' && (
                    <button
                      onClick={handleGeneratePRD}
                      className="text-xs border border-gray-300 rounded px-3 py-1.5 hover:bg-gray-50"
                    >
                      Regenerate
                    </button>
                  )}
                </div>
              </div>

              <PRDViewer
                sections={store.prdSections}
                generating={generatingPRD}
                hallucinations={store.hallucinations}
              />
            </div>

            {/* Open Questions & Risks — interactive answering panel */}
            {(conflicts.length > 0 || gaps.length > 0 || !generatingPRD) && (
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4">
                <div>
                  <h2 className="font-semibold text-gray-800">Open Questions & Risks</h2>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Review each conflict and gap below — type your answers so they can inform the next PRD revision.
                  </p>
                </div>

                <OpenQuestionsPanel
                  conflicts={conflicts as ConflictEntry[]}
                  gaps={gaps}
                  answers={store.gapAnswers}
                  onAnswerChange={store.setGapAnswer}
                  onSave={handleSaveAnswers}
                  saving={savingAnswers}
                  saved={answersSaved}
                  onRegenerate={handleGeneratePRD}
                  regenerating={generatingPRD}
                />
              </div>
            )}

            {/* Approve button */}
            {!generatingPRD && store.status === 'PRD_GENERATED' && (
              <button
                onClick={handleApprovePRD}
                className="w-full bg-green-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-green-700 transition-colors"
              >
                Approve PRD & Generate Stories →
              </button>
            )}
          </div>
        )}

        {/* Step: Stories */}
        {(currentStep === 'stories') && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4">
            <h2 className="font-semibold text-gray-800">User Stories</h2>

            {(store.status === 'PRD_APPROVED' || store.status === 'STORIES_GENERATED') && !generatingStories && (
              <button
                onClick={handleGenerateStories}
                className="w-full bg-indigo-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-indigo-700 transition-colors"
              >
                {store.stories.length > 0 ? 'Regenerate Stories →' : 'Generate User Stories →'}
              </button>
            )}

            <StoriesViewer
              stories={store.stories}
              generating={generatingStories}
              stepLabel={storyStepLabel}
            />

            {store.status === 'STORIES_GENERATED' && (
              <button
                onClick={handlePushJira}
                disabled={pushingJira}
                className="w-full bg-blue-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-blue-700 disabled:opacity-40 transition-colors"
              >
                {pushingJira ? 'Pushing to Jira…' : 'Push to Jira →'}
              </button>
            )}
          </div>
        )}

        {/* Step: Jira complete */}
        {currentStep === 'jira' && store.status === 'JIRA_PUSH_SUCCESS' && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-6">
            <div className="text-center pt-4 pb-2">
              <div className="text-5xl mb-3">🎉</div>
              <h2 className="text-xl font-bold text-gray-900">Jira Sync Complete</h2>
              <p className="text-sm text-gray-500 mt-1">
                {store.jiraResult
                  ? `${store.jiraResult.created.length} created · ${store.jiraResult.updated.length} updated · ${store.jiraResult.deleted.length} deleted`
                  : `${store.jiraKeys.length} tickets pushed`}
              </p>
            </div>

            {/* New tickets */}
            {store.jiraResult && store.jiraResult.created.length > 0 && (
              <div>
                <p className="text-xs font-bold uppercase tracking-widest text-emerald-600 mb-2 flex items-center gap-1.5">
                  <span className="inline-block w-2 h-2 rounded-full bg-emerald-500" />
                  New Tickets ({store.jiraResult.created.length})
                </p>
                <ul className="space-y-2">
                  {store.jiraResult.created.map((t) => (
                    <li key={t.key} className="flex items-center justify-between gap-3 rounded-lg bg-emerald-50 border border-emerald-100 px-3 py-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-mono text-xs font-semibold text-emerald-700 flex-shrink-0">{t.key}</span>
                        <span className="text-xs text-gray-700 truncate">{t.title}</span>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${
                          t.priority === 'high' ? 'bg-rose-100 text-rose-700' :
                          t.priority === 'low'  ? 'bg-emerald-100 text-emerald-700' :
                          'bg-amber-100 text-amber-700'
                        }`}>{t.priority}</span>
                        <a href={t.url} target="_blank" rel="noopener noreferrer"
                          className="text-xs text-emerald-600 hover:text-emerald-800 underline">
                          Open ↗
                        </a>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Updated tickets */}
            {store.jiraResult && store.jiraResult.updated.length > 0 && (
              <div>
                <p className="text-xs font-bold uppercase tracking-widest text-blue-600 mb-2 flex items-center gap-1.5">
                  <span className="inline-block w-2 h-2 rounded-full bg-blue-500" />
                  Updated Tickets ({store.jiraResult.updated.length})
                </p>
                <ul className="space-y-2">
                  {store.jiraResult.updated.map((t) => (
                    <li key={t.key} className="flex items-center justify-between gap-3 rounded-lg bg-blue-50 border border-blue-100 px-3 py-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-mono text-xs font-semibold text-blue-700 flex-shrink-0">{t.key}</span>
                        <span className="text-xs text-gray-700 truncate">{t.title}</span>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${
                          t.priority === 'high' ? 'bg-rose-100 text-rose-700' :
                          t.priority === 'low'  ? 'bg-emerald-100 text-emerald-700' :
                          'bg-amber-100 text-amber-700'
                        }`}>{t.priority}</span>
                        <a href={t.url} target="_blank" rel="noopener noreferrer"
                          className="text-xs text-blue-600 hover:text-blue-800 underline">
                          Open ↗
                        </a>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Deleted tickets */}
            {store.jiraResult && store.jiraResult.deleted.length > 0 && (
              <div>
                <p className="text-xs font-bold uppercase tracking-widest text-gray-500 mb-2 flex items-center gap-1.5">
                  <span className="inline-block w-2 h-2 rounded-full bg-gray-400" />
                  Removed Tickets ({store.jiraResult.deleted.length})
                </p>
                <ul className="space-y-2">
                  {store.jiraResult.deleted.map((t) => (
                    <li key={t.key} className="flex items-center gap-2 rounded-lg bg-gray-50 border border-gray-200 px-3 py-2">
                      <span className="font-mono text-xs font-semibold text-gray-500 line-through">{t.key}</span>
                      <span className="text-xs text-gray-500 truncate">{t.title}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Fallback: legacy plain key list when jiraResult is not set */}
            {!store.jiraResult && store.jiraKeys.length > 0 && (
              <div className="flex flex-wrap gap-2 justify-center">
                {store.jiraKeys.map((key) => (
                  <span key={key} className="bg-blue-100 text-blue-800 text-xs font-mono px-2 py-1 rounded">
                    {key}
                  </span>
                ))}
              </div>
            )}

            {/* Regenerate stories button */}
            <div className="border-t border-gray-100 pt-4">
              <button
                onClick={handleGenerateStories}
                disabled={generatingStories}
                className="w-full border border-gray-300 text-gray-600 rounded-lg py-2 text-sm font-medium hover:bg-gray-50 disabled:opacity-40 transition-colors"
              >
                {generatingStories ? 'Regenerating…' : 'Regenerate Stories & Sync Again →'}
              </button>
            </div>
          </div>
        )}

        {/* Jira push error: stories still showing */}
        {currentStep === 'jira' && store.status !== 'JIRA_PUSH_SUCCESS' && store.stories.length > 0 && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4">
            <h2 className="font-semibold text-gray-800">User Stories</h2>
            <StoriesViewer stories={store.stories} generating={false} />
            {jiraError && (
              <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-3">
                Jira error: {jiraError}
              </div>
            )}
            <button
              onClick={handlePushJira}
              disabled={pushingJira}
              className="w-full bg-blue-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-blue-700 disabled:opacity-40 transition-colors"
            >
              {pushingJira ? 'Retrying Jira push…' : 'Publish Jira Tickets →'}
            </button>
          </div>
        )}
      </div>
    </main>
  )
}
