'use client'

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type WorkflowStatus =
  | 'TRANSCRIPT_UPLOADED'
  | 'PRD_GENERATED'
  | 'PRD_APPROVED'
  | 'STORIES_GENERATED'
  | 'JIRA_PUSH_PENDING'
  | 'JIRA_PUSH_SUCCESS'
  | 'COMPLETED'

export interface PRDSections {
  title?: { title: string; subtitle?: string }
  description?: { overview: string }
  problem?: { problem_statement: string; pain_points: string[] }
  why?: { rationale: string; business_value: string }
  success?: { metrics: string[]; kpis: string[] }
  audience?: { primary_audience: string; personas: string[] }
  open_questions?: { type1_conflicts: unknown[]; type2_gaps: string[] }
}

export interface UserStory {
  id: string
  title: string
  description: string
  acceptance_criteria: string[]
  validations: { field: string; rule: string; error_message: string }[]
}

interface PipelineState {
  // Persisted
  projectId: string | null

  // Derived from server — not persisted
  projectName: string
  status: WorkflowStatus | null
  prdSections: PRDSections
  stories: UserStory[]
  jiraKeys: string[]
  hallucinations: number

  // Actions
  setProjectId: (id: string) => void
  setProjectName: (name: string) => void
  setStatus: (status: WorkflowStatus) => void
  upsertPRDSection: (key: string, data: unknown) => void
  addStory: (story: UserStory) => void
  setJiraKeys: (keys: string[]) => void
  setHallucinations: (count: number) => void
  rehydrateFromServer: (project: {
    id: string
    name: string
    status: WorkflowStatus
    prd_json?: string
  }) => void
  reset: () => void
}

const initialState = {
  projectId: null,
  projectName: '',
  status: null,
  prdSections: {},
  stories: [],
  jiraKeys: [],
  hallucinations: 0,
}

export const usePipelineStore = create<PipelineState>()(
  persist(
    (set) => ({
      ...initialState,

      setProjectId: (id) => set({ projectId: id }),
      setProjectName: (name) => set({ projectName: name }),
      setStatus: (status) => set({ status }),

      upsertPRDSection: (key, data) =>
        set((state) => ({
          prdSections: { ...state.prdSections, [key]: data },
        })),

      addStory: (story) =>
        set((state) => ({ stories: [...state.stories, story] })),

      setJiraKeys: (keys) => set({ jiraKeys: keys }),
      setHallucinations: (count) => set({ hallucinations: count }),

      rehydrateFromServer: (project) => {
        const prdSections = project.prd_json
          ? (() => {
              try { return JSON.parse(project.prd_json) } catch { return {} }
            })()
          : {}
        set({
          projectId: project.id,
          projectName: project.name,
          status: project.status,
          prdSections,
        })
      },

      reset: () => set(initialState),
    }),
    {
      name: 'dochub-pipeline',
      // Only persist projectId — everything else derived from server
      partialize: (state) => ({ projectId: state.projectId }),
    }
  )
)
