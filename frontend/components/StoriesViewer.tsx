'use client'

import { UserStory, TranscriptRef } from '@/store/pipelineStore'

interface StoriesViewerProps {
  stories: UserStory[]
  generating: boolean
  stepLabel?: string
}

const PRIORITY_STYLES: Record<string, string> = {
  high: 'bg-rose-100 text-rose-700 border border-rose-200',
  medium: 'bg-amber-100 text-amber-700 border border-amber-200',
  low: 'bg-emerald-100 text-emerald-700 border border-emerald-200',
}

const SIZE_STYLES: Record<string, string> = {
  XS: 'bg-slate-100 text-slate-600 border border-slate-200',
  S:  'bg-sky-100 text-sky-700 border border-sky-200',
  M:  'bg-violet-100 text-violet-700 border border-violet-200',
  L:  'bg-orange-100 text-orange-700 border border-orange-200',
  XL: 'bg-red-100 text-red-700 border border-red-200',
}

const SIZE_LABELS: Record<string, string> = {
  XS: 'XS · <1 day',
  S:  'S · 1-2 days',
  M:  'M · 3-5 days',
  L:  'L · 6-10 days',
  XL: 'XL · >2 wks',
}

const DIFF_STYLES: Record<string, string> = {
  new:      'bg-emerald-100 text-emerald-700 border border-emerald-200',
  modified: 'bg-blue-100 text-blue-700 border border-blue-200',
  kept:     'bg-gray-100 text-gray-500 border border-gray-200',
}

const DIFF_LABELS: Record<string, string> = {
  new:      '✦ New',
  modified: '↺ Updated',
  kept:     '✓ Kept',
}

function SectionLabel({ label }: { label: string }) {
  return (
    <p className="text-xs font-bold uppercase tracking-widest text-gray-400 mb-2">
      {label}
    </p>
  )
}

function StoryCard({ story }: { story: UserStory }) {
  const priority = story.priority ?? 'medium'
  const size = story.size ?? 'M'

  return (
    <div className="border border-gray-200 rounded-xl p-5 bg-white shadow-sm space-y-4 hover:shadow-md transition-shadow">
      {/* Header row: badges */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex gap-1.5 flex-shrink-0 flex-wrap">
          {story.diff && (
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${DIFF_STYLES[story.diff] ?? ''}`}>
              {DIFF_LABELS[story.diff] ?? story.diff}
            </span>
          )}
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${PRIORITY_STYLES[priority] ?? PRIORITY_STYLES.medium}`}>
            {priority}
          </span>
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${SIZE_STYLES[size] ?? SIZE_STYLES.M}`}>
            {SIZE_LABELS[size] ?? size}
          </span>
        </div>
      </div>

      {/* Title */}
      <div>
        <SectionLabel label="Title" />
        <h3 className="font-semibold text-gray-900 text-sm leading-snug">{story.title}</h3>
      </div>

      {/* Description */}
      <div>
        <SectionLabel label="Description" />
        <p className="text-sm text-gray-600 italic leading-relaxed">{story.description}</p>
      </div>

      {/* Acceptance Criteria */}
      {story.acceptance_criteria.length > 0 && (
        <div>
          <SectionLabel label="Acceptance Criteria" />
          <ul className="space-y-1">
            {story.acceptance_criteria.map((ac, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-gray-700">
                <span className="text-indigo-400 mt-0.5 flex-shrink-0">•</span>
                <span>{ac}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Validations */}
      {story.validations.length > 0 && (
        <div>
          <SectionLabel label="Validations" />
          <div className="rounded-lg border border-gray-100 overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-100">
                  <th className="px-3 py-2 text-left font-semibold text-gray-600">Field</th>
                  <th className="px-3 py-2 text-left font-semibold text-gray-600">Rule</th>
                  <th className="px-3 py-2 text-left font-semibold text-gray-600">Error</th>
                </tr>
              </thead>
              <tbody>
                {story.validations.map((v, i) => (
                  <tr key={i} className="border-b border-gray-50 last:border-0">
                    <td className="px-3 py-2 text-gray-800 font-mono">{v.field}</td>
                    <td className="px-3 py-2 text-gray-700">{v.rule}</td>
                    <td className="px-3 py-2 text-gray-600">{v.error_message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Dependencies */}
      {story.dependencies && story.dependencies.length > 0 && (
        <div>
          <SectionLabel label="Dependencies" />
          <ul className="space-y-1">
            {story.dependencies.map((dep, i) => (
              <li key={i} className="text-xs text-gray-600 flex items-center gap-1.5">
                <span className="text-gray-300">→</span>
                {dep}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Reference Links */}
      {story.reference_links && story.reference_links.length > 0 && (
        <div>
          <SectionLabel label="Reference Links" />
          <ul className="space-y-0.5">
            {story.reference_links.map((ref, i) => (
              <li key={i} className="text-xs text-indigo-600 break-all">{ref}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Transcript References */}
      {story.transcript_references && story.transcript_references.length > 0 && (
        <div>
          <SectionLabel label="Transcript References" />
          <ul className="space-y-2">
            {story.transcript_references.map((ref: TranscriptRef, i: number) => (
              <li key={i} className="rounded-lg bg-amber-50 border border-amber-100 px-3 py-2 text-xs">
                {ref.speaker && (
                  <span className="font-semibold text-amber-700 mr-1">{ref.speaker}:</span>
                )}
                <span className="text-gray-700 italic">&ldquo;{ref.excerpt}&rdquo;</span>
                <span className="ml-1.5 text-amber-500 font-mono text-[10px]">[{ref.source}]</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function SkeletonStory() {
  return (
    <div className="border border-gray-100 rounded-xl p-5 bg-white animate-pulse space-y-3">
      <div className="flex items-start justify-between">
        <div className="h-4 bg-gray-200 rounded w-2/3" />
        <div className="h-5 bg-gray-100 rounded-full w-14" />
      </div>
      <div className="h-3 bg-gray-100 rounded w-full" />
      <div className="h-3 bg-gray-100 rounded w-5/6" />
      <div className="space-y-1.5 pt-1">
        <div className="h-2.5 bg-gray-100 rounded w-full" />
        <div className="h-2.5 bg-gray-100 rounded w-4/5" />
      </div>
    </div>
  )
}

export function StoriesViewer({ stories, generating, stepLabel }: StoriesViewerProps) {
  return (
    <div className="space-y-4">
      {stepLabel && (
        <div className="flex items-center gap-2.5 text-sm text-indigo-600 bg-indigo-50 border border-indigo-100 rounded-lg px-4 py-2.5">
          <svg className="w-4 h-4 animate-spin flex-shrink-0" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          <span className="font-medium">{stepLabel}</span>
        </div>
      )}

      {stories.map((story) => (
        <StoryCard key={story.id} story={story} />
      ))}

      {generating && stories.length === 0 && (
        <>
          {[0, 1, 2].map((i) => <SkeletonStory key={i} />)}
        </>
      )}
    </div>
  )
}
