'use client'

import { cn } from '@/lib/utils'

export type Step = {
  id: string
  label: string
}

export const STEPS: Step[] = [
  { id: 'upload', label: 'Upload Transcript' },
  { id: 'prd', label: 'Generate PRD' },
  { id: 'review', label: 'Review & Approve' },
  { id: 'stories', label: 'Generate Stories' },
  { id: 'jira', label: 'Push to Jira' },
]

interface WizardStepperProps {
  currentStep: string
}

export function WizardStepper({ currentStep }: WizardStepperProps) {
  const currentIdx = STEPS.findIndex((s) => s.id === currentStep)

  return (
    <nav className="flex items-center gap-0 w-full mb-8">
      {STEPS.map((step, idx) => {
        const isCompleted = idx < currentIdx
        const isCurrent = idx === currentIdx

        return (
          <div key={step.id} className="flex items-center flex-1 last:flex-none">
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  'w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold border-2 transition-colors',
                  isCompleted && 'bg-blue-600 border-blue-600 text-white',
                  isCurrent && 'bg-white border-blue-600 text-blue-600',
                  !isCompleted && !isCurrent && 'bg-white border-gray-300 text-gray-400'
                )}
              >
                {isCompleted ? '✓' : idx + 1}
              </div>
              <span
                className={cn(
                  'mt-1 text-xs font-medium whitespace-nowrap',
                  isCurrent && 'text-blue-600',
                  isCompleted && 'text-blue-600',
                  !isCompleted && !isCurrent && 'text-gray-400'
                )}
              >
                {step.label}
              </span>
            </div>
            {idx < STEPS.length - 1 && (
              <div
                className={cn(
                  'flex-1 h-0.5 mx-2 mb-5 transition-colors',
                  idx < currentIdx ? 'bg-blue-600' : 'bg-gray-200'
                )}
              />
            )}
          </div>
        )
      })}
    </nav>
  )
}
