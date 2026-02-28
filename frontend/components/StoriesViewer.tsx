'use client'

import { UserStory } from '@/store/pipelineStore'

interface StoriesViewerProps {
  stories: UserStory[]
  generating: boolean
  stepLabel?: string
}

function StoryCard({ story }: { story: UserStory }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 space-y-3">
      <h3 className="font-semibold text-gray-900">{story.title}</h3>
      <p className="text-sm text-gray-700 italic">{story.description}</p>

      {story.acceptance_criteria.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">
            Acceptance Criteria
          </p>
          <ul className="list-disc list-inside space-y-0.5">
            {story.acceptance_criteria.map((ac, i) => (
              <li key={i} className="text-sm text-gray-700">{ac}</li>
            ))}
          </ul>
        </div>
      )}

      {story.validations.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">
            Validations
          </p>
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-gray-50">
                <th className="border border-gray-200 px-2 py-1 text-left">Field</th>
                <th className="border border-gray-200 px-2 py-1 text-left">Rule</th>
                <th className="border border-gray-200 px-2 py-1 text-left">Error</th>
              </tr>
            </thead>
            <tbody>
              {story.validations.map((v, i) => (
                <tr key={i}>
                  <td className="border border-gray-200 px-2 py-1">{v.field}</td>
                  <td className="border border-gray-200 px-2 py-1">{v.rule}</td>
                  <td className="border border-gray-200 px-2 py-1">{v.error_message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function SkeletonStory() {
  return (
    <div className="border border-gray-200 rounded-lg p-4 animate-pulse space-y-2">
      <div className="h-4 bg-gray-200 rounded w-2/3" />
      <div className="h-3 bg-gray-100 rounded w-full" />
      <div className="h-3 bg-gray-100 rounded w-5/6" />
    </div>
  )
}

export function StoriesViewer({ stories, generating, stepLabel }: StoriesViewerProps) {
  return (
    <div className="space-y-4">
      {stepLabel && (
        <div className="flex items-center gap-2 text-sm text-blue-600">
          <span className="animate-spin">⟳</span>
          <span>{stepLabel}</span>
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
