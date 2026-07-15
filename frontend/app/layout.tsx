import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { themeInitScript } from "@/components/layout/ThemeToggle";
import { AuthProvider } from "@/hooks/useAuth";
import "./globals.css";

const sans = Inter({
  variable: "--font-sans-stack",
  subsets: ["latin"],
  display: "swap",
});

const mono = JetBrains_Mono({
  variable: "--font-mono-stack",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Anchor — Your private knowledge, grounded answers",
    template: "%s · Anchor",
  },
  description:
    "Anchor answers questions from your private document knowledge base using retrieval-augmented generation, with source citations, model routing, tool calling and output guardrails.",
  applicationName: "Anchor",
  openGraph: {
    title: "Anchor — Your private knowledge, grounded answers",
    description:
      "Query private documentation with retrieval-augmented generation, source citations and guardrails.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#faf9f7" },
    { media: "(prefers-color-scheme: dark)", color: "#100f0e" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} h-full antialiased`}>
      <head>
        {/* Applies the stored theme before first paint to avoid a flash. */}
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="min-h-full bg-bg font-sans text-fg">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
