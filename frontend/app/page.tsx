'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { usePipelineStore } from '@/store/pipelineStore'

export default function Home() {
  const [name, setName] = useState('')
  const [userName, setUserNameLocal] = useState('')
  const router = useRouter()
  const setProjectName = usePipelineStore((s) => s.setProjectName)
  const setUserName = usePipelineStore((s) => s.setUserName)

  const handleStart = () => {
    const trimmed = name.trim()
    if (!trimmed) return
    setProjectName(trimmed)
    setUserName(userName.trim())
    router.push('/workflow')
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50">
      <div className="w-full max-w-md px-4">
        {/* Logo / branding */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-indigo-600 shadow-lg shadow-indigo-200 mb-4">
            <span className="text-3xl">📄</span>
          </div>
          <h1 className="text-3xl font-bold text-gray-900 tracking-tight">DocHub</h1>
          <p className="text-gray-500 mt-2 text-sm leading-relaxed max-w-xs mx-auto">
            Turn meeting transcripts into context-aware PRDs and Jira tickets — in minutes.
          </p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-xl shadow-gray-200/60 border border-gray-100 p-8">
          <div className="space-y-5">
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-1.5">
                Project Name
              </label>
              <input
                type="text"
                className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
                placeholder="e.g. Payments v3 Checkout"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleStart()}
                autoFocus
              />
              <p className="text-xs text-gray-400 mt-1.5">
                Give the project a descriptive name — it helps the AI generate better context.
              </p>
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-1.5">
                Your Name
              </label>
              <input
                type="text"
                className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
                placeholder="e.g. Sarah Chen"
                value={userName}
                onChange={(e) => setUserNameLocal(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleStart()}
              />
              <p className="text-xs text-gray-400 mt-1.5">
                Used to track who initiated this project and made decisions.
              </p>
            </div>

            <button
              onClick={handleStart}
              disabled={!name.trim()}
              className="w-full bg-indigo-600 text-white rounded-xl py-3 text-sm font-semibold hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-sm shadow-indigo-200 hover:shadow-md hover:shadow-indigo-200"
            >
              Start Pipeline →
            </button>
          </div>
        </div>

        {/* Pipeline steps hint */}
        <div className="flex items-center justify-center gap-2 mt-6 text-xs text-gray-400">
          {['Upload', 'PRD Review', 'Stories', 'Jira'].map((step, i, arr) => (
            <span key={step} className="flex items-center gap-2">
              <span>{step}</span>
              {i < arr.length - 1 && <span className="text-gray-300">→</span>}
            </span>
          ))}
        </div>
      </div>
    </main>
  )
}
