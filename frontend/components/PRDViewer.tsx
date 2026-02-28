'use client'

import ReactMarkdown from 'react-markdown'
import { PRDSections, ConflictEntry, GapEntry } from '@/store/pipelineStore'

// open_questions is excluded here — it is shown as an interactive panel
// (OpenQuestionsPanel) in the workflow page so users can answer gaps/conflicts.
const SECTION_ORDER = [
  'title',
  'description',
  'problem',
  'why',
  'success',
  'audience',
] as const

const SECTION_LABELS: Record<string, string> = {
  title: 'Project Title',
  description: 'Description',
  problem: 'Problem Statement',
  why: 'Why Now',
  success: 'Success Metrics',
  audience: 'Target Audience',
}

const SECTION_ICONS: Record<string, string> = {
  title: '📌',
  description: '📝',
  problem: '🎯',
  why: '💡',
  success: '📊',
  audience: '👥',
}

const SECTION_ACCENT: Record<string, string> = {
  title: 'border-l-indigo-500',
  description: 'border-l-blue-400',
  problem: 'border-l-rose-400',
  why: 'border-l-amber-400',
  success: 'border-l-emerald-500',
  audience: 'border-l-purple-400',
}

function sectionToMarkdown(key: string, data: unknown): string {
  if (!data || typeof data !== 'object') return ''
  const d = data as Record<string, unknown>

  switch (key) {
    case 'title':
      return `# ${d.title ?? ''}\n\n${d.subtitle ? `*${d.subtitle}*` : ''}`
    case 'description':
      return String(d.overview ?? '')
    case 'problem': {
      const pts = Array.isArray(d.pain_points) ? d.pain_points : []
      return `${d.problem_statement ?? ''}\n\n${pts.map((p) => `- ${p}`).join('\n')}`
    }
    case 'why':
      return `**Rationale:** ${d.rationale ?? ''}\n\n**Business Value:** ${d.business_value ?? ''}`
    case 'success': {
      const metrics = Array.isArray(d.metrics) ? d.metrics : []
      const kpis = Array.isArray(d.kpis) ? d.kpis : []
      return `**Metrics:**\n${metrics.map((m) => `- ${m}`).join('\n')}\n\n**KPIs:**\n${kpis.map((k) => `- ${k}`).join('\n')}`
    }
    case 'audience': {
      const personas = Array.isArray(d.personas) ? d.personas : []
      return `**Primary:** ${d.primary_audience ?? ''}${d.secondary_audience ? `\n\n**Secondary:** ${d.secondary_audience}` : ''}\n\n${personas.map((p) => `- ${p}`).join('\n')}`
    }
    default:
      return JSON.stringify(data, null, 2)
  }
}

function SkeletonSection({ label }: { label: string }) {
  return (
    <div className="border border-gray-100 rounded-xl p-5 animate-pulse bg-white">
      <div className="flex items-center gap-2 mb-4">
        <div className="h-4 w-4 bg-gray-200 rounded" />
        <div className="h-4 bg-gray-200 rounded w-28" />
      </div>
      <div className="space-y-2">
        <div className="h-3 bg-gray-100 rounded w-full" />
        <div className="h-3 bg-gray-100 rounded w-5/6" />
        <div className="h-3 bg-gray-100 rounded w-4/6" />
      </div>
      <p className="text-xs text-gray-400 mt-3">Generating {label}…</p>
    </div>
  )
}

interface PRDViewerProps {
  sections: PRDSections
  generating: boolean
  hallucinations?: number
}

export function PRDViewer({ sections, generating, hallucinations = 0 }: PRDViewerProps) {
  const completedKeys = Object.keys(sections)

  return (
    <div className="space-y-3">
      {hallucinations > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 flex items-center gap-2">
          <span className="text-lg">⚠️</span>
          <span className="text-sm text-amber-800 font-medium">
            {hallucinations} numeric claim{hallucinations !== 1 ? 's' : ''} not found in transcript — verify before approving.
          </span>
        </div>
      )}

      {SECTION_ORDER.map((key) => {
        const data = sections[key]
        const isGenerating = generating && !data

        if (isGenerating) {
          return <SkeletonSection key={key} label={SECTION_LABELS[key]} />
        }
        if (!data) return null

        const md = sectionToMarkdown(key, data)
        const accent = SECTION_ACCENT[key] ?? 'border-l-gray-300'

        return (
          <div key={key} className={`border border-gray-100 border-l-4 ${accent} rounded-xl p-5 bg-white shadow-sm`}>
            <div className="flex items-center gap-2 mb-3">
              <span className="text-base">{SECTION_ICONS[key]}</span>
              <h3 className="text-xs font-bold text-gray-500 uppercase tracking-widest">
                {SECTION_LABELS[key]}
              </h3>
            </div>
            <div className="markdown-prose text-sm text-gray-800">
              <ReactMarkdown>{md}</ReactMarkdown>
            </div>
          </div>
        )
      })}

      {generating && completedKeys.length === 0 && (
        <>
          {SECTION_ORDER.map((key) => (
            <SkeletonSection key={key} label={SECTION_LABELS[key]} />
          ))}
        </>
      )}
    </div>
  )
}
