import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'MarketDiff — what changed since you last looked',
  description:
    'A watchlist that tells you what meaningfully changed since your last visit, and why it deserves your attention.',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#f2f1ec',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Loaded over the network with a full system fallback stack in CSS, so
            the app still renders correctly with fonts blocked or offline. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font --
            the rule targets the pages router; in the app router this <head>
            lives in the root layout and applies to every route. */}
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <a className="skip-link" href="#feed">
          Skip to the change feed
        </a>
        {children}
      </body>
    </html>
  );
}
