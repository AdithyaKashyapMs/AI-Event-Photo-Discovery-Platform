import type { Metadata } from 'next';
import localFont from 'next/font/local';
import './globals.css';
import Navbar from '../components/Navbar';

// Rounded, friendly display face for headings; Nunito for body text. Both are
// SIL Open Font License fonts, self-hosted so builds need no network access.
const baloo = localFont({
  src: '../fonts/baloo2-latin-variable.woff2',
  weight: '600 800',
  variable: '--font-baloo',
  display: 'swap',
});
const nunito = localFont({
  src: '../fonts/nunito-latin-variable.woff2',
  weight: '400 800',
  variable: '--font-nunito',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'EventSnap - Find every photo you are in',
  description: 'Take a quick live selfie and get every event photo you appear in. Only you can see them.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${baloo.variable} ${nunito.variable}`}>
      <body className="min-h-screen bg-paper text-ink">
        <Navbar />
        <main>{children}</main>
      </body>
    </html>
  );
}
