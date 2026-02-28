'use client'

import { useCallback, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface UploadResult {
  project_id: string
  transcript_text: string
}

interface AddTranscriptsResult {
  transcript_ids: string[]
  merged_length: number
}

export function useFileUpload() {
  const [uploading, setUploading] = useState(false)
  const [addingTranscripts, setAddingTranscripts] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const upload = useCallback(
    async (file: File, projectName: string): Promise<UploadResult | null> => {
      setUploading(true)
      setError(null)
      try {
        const fd = new FormData()
        fd.append('file', file)
        fd.append('project_name', projectName)

        const res = await fetch(`${API}/upload`, { method: 'POST', body: fd })
        if (!res.ok) {
          const msg = await res.text()
          throw new Error(`Upload failed: ${msg}`)
        }
        return (await res.json()) as UploadResult
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        return null
      } finally {
        setUploading(false)
      }
    },
    []
  )

  const uploadAdditional = useCallback(
    async (files: File[], projectId: string): Promise<AddTranscriptsResult | null> => {
      setAddingTranscripts(true)
      setError(null)
      try {
        const fd = new FormData()
        for (const f of files) fd.append('files', f)

        const res = await fetch(`${API}/upload/transcripts/${projectId}`, {
          method: 'POST',
          body: fd,
        })
        if (!res.ok) {
          const msg = await res.text()
          throw new Error(`Add transcripts failed: ${msg}`)
        }
        return (await res.json()) as AddTranscriptsResult
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        return null
      } finally {
        setAddingTranscripts(false)
      }
    },
    []
  )

  return { upload, uploading, uploadAdditional, addingTranscripts, error }
}
