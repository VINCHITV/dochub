'use client'

import { ConflictEntry, GapEntry } from '@/store/pipelineStore'

const SEVERITY_STYLES: Record<string, string> = {
  blocking: 'bg-red-100 text-red-700 border-red-200',
  needs_discussion: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  minor: 'bg-blue-100 text-blue-700 border-blue-200',
}

interface OpenQuestionsPanelProps {
  conflicts: ConflictEntry[]
  gaps: (GapEntry | string)[]
  answers: Record<string, string>
  onAnswerChange: (key: string, value: string) => void
  onSave: () => void
  saving: boolean
  saved: boolean
  onRegenerate?: () => void
  regenerating?: boolean
}

function ConflictItem({
  conflict,
  answer,
  onChange,
}: {
  conflict: ConflictEntry
  answer: string
  onChange: (val: string) => void
}) {
  const key = `conflict:${conflict.source_prd_id}`
  const sev = conflict.severity ?? 'minor'
  return (
    <div className="border border-gray-200 rounded-lg p-4 space-y-2">
      <div className="flex items-center gap-2">
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${SEVERITY_STYLES[sev] ?? SEVERITY_STYLES.minor}`}>
          {sev.replace('_', ' ')}
        </span>
        <span className="text-xs text-gray-400 font-mono">{conflict.source_prd_id}</span>
      </div>

      <p className="text-sm text-gray-800 font-medium">{conflict.conflicting_statement}</p>
      <p className="text-sm text-gray-600">
        <span className="font-semibold text-gray-500">Proposed: </span>
        {conflict.proposed_change}
      </p>

      {conflict.kb_excerpt && (
        <blockquote className="border-l-4 border-gray-300 pl-3 text-xs text-gray-500 italic">
          {conflict.kb_excerpt}
        </blockquote>
      )}

      <div>
        <label htmlFor={key} className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Your decision / response
        </label>
        <textarea
          id={key}
          value={answer}
          onChange={(e) => onChange(e.target.value)}
          rows={2}
          placeholder="Accept, reject, or clarify…"
          className="mt-1 w-full text-sm border border-gray-200 rounded-md px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
      </div>
    </div>
  )
}

function GapItem({
  gap,
  answer,
  onChange,
}: {
  gap: GapEntry | string
  answer: string
  onChange: (val: string) => void
}) {
  const question = typeof gap === 'string' ? gap : gap.question
  const excerpt = typeof gap === 'string' ? undefined : gap.transcript_excerpt
  const key = `gap:${question}`

  return (
    <div className="border border-gray-200 rounded-lg p-4 space-y-2">
      <p className="text-sm text-gray-800 font-medium">{question}</p>

      {excerpt && (
        <blockquote className="border-l-4 border-gray-300 pl-3 text-xs text-gray-500 italic">
          {excerpt}
        </blockquote>
      )}

      <div>
        <label htmlFor={key} className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Your answer
        </label>
        <textarea
          id={key}
          value={answer}
          onChange={(e) => onChange(e.target.value)}
          rows={2}
          placeholder="Type your answer or note it's not applicable…"
          className="mt-1 w-full text-sm border border-gray-200 rounded-md px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
      </div>
    </div>
  )
}

export function OpenQuestionsPanel({
  conflicts,
  gaps,
  answers,
  onAnswerChange,
  onSave,
  saving,
  saved,
  onRegenerate,
  regenerating,
}: OpenQuestionsPanelProps) {
  const hasContent = conflicts.length > 0 || gaps.length > 0

  if (!hasContent) {
    return (
      <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-sm text-green-700">
        No open questions or conflicts detected — the PRD is self-contained.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {conflicts.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
            KB Conflicts ({conflicts.length})
          </h3>
          {conflicts.map((c, i) => (
            <ConflictItem
              key={i}
              conflict={c}
              answer={answers[`conflict:${c.source_prd_id}`] ?? ''}
              onChange={(val) => onAnswerChange(`conflict:${c.source_prd_id}`, val)}
            />
          ))}
        </div>
      )}

      {gaps.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
            Transcript Gaps ({gaps.length})
          </h3>
          {gaps.map((g, i) => {
            const question = typeof g === 'string' ? g : g.question
            return (
              <GapItem
                key={i}
                gap={g}
                answer={answers[`gap:${question}`] ?? ''}
                onChange={(val) => onAnswerChange(`gap:${question}`, val)}
              />
            )
          })}
        </div>
      )}

      <div className="flex items-center gap-3 pt-1 flex-wrap">
        <button
          onClick={onSave}
          disabled={saving || regenerating}
          className="bg-indigo-600 text-white text-sm font-semibold rounded-lg px-4 py-2 hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {saving ? 'Saving…' : 'Save Answers'}
        </button>

        {saved && onRegenerate && (
          <button
            onClick={onRegenerate}
            disabled={regenerating}
            className="bg-green-600 text-white text-sm font-semibold rounded-lg px-4 py-2 hover:bg-green-700 disabled:opacity-40 transition-colors"
          >
            {regenerating ? 'Regenerating PRD…' : '↻ Regenerate PRD with Answers'}
          </button>
        )}

        {saved && !onRegenerate && (
          <span className="text-sm text-green-600 font-medium">✓ Answers saved</span>
        )}
      </div>
    </div>
  )
}
