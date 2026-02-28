import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'DocHub — Transcript to PRD',
  description: 'Turn meeting transcripts into context-aware PRDs and Jira tickets',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-50">{children}</body>
    </html>
  )
}
