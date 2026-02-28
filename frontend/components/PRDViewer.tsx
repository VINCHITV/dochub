'use client'

import ReactMarkdown from 'react-markdown'
import { PRDSections } from '@/store/pipelineStore'

const SECTION_ORDER = [
  'title',
  'description',
  'problem',
  'why',
  'success',
  'audience',
  'open_questions',
] as const

const SECTION_LABELS: Record<string, string> = {
  title: 'Project Title',
  description: 'Description',
  problem: 'Problem Statement',
  why: 'Why Now',
  success: 'Success Metrics',
  audience: 'Target Audience',
  open_questions: 'Open Questions & Risks',
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
      return `**Primary:** ${d.primary_audience ?? ''}\n${d.secondary_audience ? `\n**Secondary:** ${d.secondary_audience}` : ''}\n\n${personas.map((p) => `- ${p}`).join('\n')}`
    }
    case 'open_questions': {
      const gaps = Array.isArray(d.type2_gaps) ? d.type2_gaps : []
      const conflicts = Array.isArray(d.type1_conflicts) ? d.type1_conflicts : []
      return [
        conflicts.length > 0 ? `**KB Conflicts:**\n${conflicts.map((c: unknown) => {
          const conflict = c as Record<string, unknown>
          return `- [${conflict.severity}] ${conflict.conflicting_statement} → ${conflict.proposed_change}`
        }).join('\n')}` : '',
        gaps.length > 0 ? `**Transcript Gaps:**\n${gaps.map((g) => `- ${g}`).join('\n')}` : '',
      ].filter(Boolean).join('\n\n')
    }
    default:
      return JSON.stringify(data, null, 2)
  }
}

function SkeletonSection({ label }: { label: string }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 animate-pulse">
      <div className="h-5 bg-gray-200 rounded w-1/3 mb-3" />
      <div className="space-y-2">
        <div className="h-3 bg-gray-100 rounded w-full" />
        <div className="h-3 bg-gray-100 rounded w-5/6" />
        <div className="h-3 bg-gray-100 rounded w-4/6" />
      </div>
      <p className="text-xs text-gray-400 mt-2">Generating {label}…</p>
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
    <div className="space-y-4">
      {hallucinations > 0 && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 flex items-center gap-2">
          <span className="text-yellow-600 text-sm font-medium">
            ⚠ {hallucinations} numeric claim{hallucinations !== 1 ? 's' : ''} not found in transcript
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

        return (
          <div key={key} className="border border-gray-200 rounded-lg p-4">
            <h3 className="font-semibold text-gray-800 mb-2 text-sm uppercase tracking-wide">
              {SECTION_LABELS[key]}
            </h3>
            <div className="prose prose-sm max-w-none text-gray-700">
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
