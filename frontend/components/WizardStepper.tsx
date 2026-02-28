'use client'

import { cn } from '@/lib/utils'

export type Step = {
  id: string
  label: string
}

export const STEPS: Step[] = [
  { id: 'upload', label: 'Upload' },
  { id: 'review', label: 'PRD Review' },
  { id: 'stories', label: 'Stories' },
  { id: 'jira', label: 'Jira Push' },
]

interface WizardStepperProps {
  currentStep: string
}

export function WizardStepper({ currentStep }: WizardStepperProps) {
  const currentIdx = STEPS.findIndex((s) => s.id === currentStep)

  return (
    <div className="mb-8">
      <nav className="flex items-center">
        {STEPS.map((step, idx) => {
          const isCompleted = idx < currentIdx
          const isCurrent = idx === currentIdx

          return (
            <div key={step.id} className="flex items-center flex-1 last:flex-none">
              <div className="flex flex-col items-center gap-1.5">
                <div
                  className={cn(
                    'w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold border-2 transition-all duration-200',
                    isCompleted && 'bg-indigo-600 border-indigo-600 text-white shadow-sm shadow-indigo-200',
                    isCurrent && 'bg-white border-indigo-600 text-indigo-600 shadow-md shadow-indigo-100',
                    !isCompleted && !isCurrent && 'bg-white border-gray-200 text-gray-400'
                  )}
                >
                  {isCompleted ? (
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                  ) : (
                    idx + 1
                  )}
                </div>
                <span
                  className={cn(
                    'text-xs font-medium whitespace-nowrap',
                    isCurrent && 'text-indigo-600',
                    isCompleted && 'text-indigo-500',
                    !isCompleted && !isCurrent && 'text-gray-400'
                  )}
                >
                  {step.label}
                </span>
              </div>
              {idx < STEPS.length - 1 && (
                <div
                  className={cn(
                    'flex-1 h-0.5 mx-3 mb-5 rounded-full transition-colors duration-300',
                    idx < currentIdx ? 'bg-indigo-600' : 'bg-gray-200'
                  )}
                />
              )}
            </div>
          )
        })}
      </nav>
    </div>
  )
}
