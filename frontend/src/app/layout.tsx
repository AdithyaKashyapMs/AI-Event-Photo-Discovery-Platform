import type { Metadata } from 'next';
import './globals.css';
import Navbar from '../components/Navbar';

// App-wide metadata for SEO
export const metadata: Metadata = {
  title: 'EventSnap - AI Photo Discovery',
  description: 'Find every event photo you appear in with AI-powered facial recognition.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 text-white min-h-screen">
        <Navbar />
        <main>{children}</main>
      </body>
    </html>
  );
}