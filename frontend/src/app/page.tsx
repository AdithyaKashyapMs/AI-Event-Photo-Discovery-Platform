import Link from 'next/link';

export default function HomePage() {
  return (
    <div className="min-h-screen">
      {/* Hero Section */}
      <section className="py-20 px-4 text-center">
        <h1 className="text-5xl font-bold text-white mb-6">
          Find Every Photo You're In
        </h1>
        <p className="text-xl text-gray-400 max-w-2xl mx-auto mb-8">
          AI-powered facial recognition finds your photos across entire events.
          Upload a selfie and instantly discover every moment captured.
        </p>
        <div className="flex gap-4 justify-center">
          <Link
            href="/auth/register"
            className="bg-blue-600 text-white px-6 py-3 rounded-lg text-lg hover:bg-blue-700"
          >
            Get Started
          </Link>
          <Link
            href="/auth/login"
            className="border border-gray-700 text-gray-300 px-6 py-3 rounded-lg text-lg hover:bg-gray-800"
          >
            Login
          </Link>
        </div>
      </section>

      {/* Feature Highlights */}
      <section className="py-16 px-4 max-w-6xl mx-auto">
        <div className="grid md:grid-cols-3 gap-8">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center">
            <div className="text-4xl mb-4">🔍</div>
            <h3 className="text-lg font-semibold text-white mb-2">
              Facial Recognition
            </h3>
            <p className="text-gray-400">
              Advanced AI detects and matches faces across thousands of event photos.
            </p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center">
            <div className="text-4xl mb-4">⚡</div>
            <h3 className="text-lg font-semibold text-white mb-2">
              Instant Gallery
            </h3>
            <p className="text-gray-400">
              Submit a selfie and see your personalized photo gallery in seconds.
            </p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center">
            <div className="text-4xl mb-4">📱</div>
            <h3 className="text-lg font-semibold text-white mb-2">
              QR Code Join
            </h3>
            <p className="text-gray-400">
              Attendees join events instantly by scanning a QR code or clicking a link.
            </p>
          </div>
        </div>
      </section>

      {/* Call to Action */}
      <section className="py-16 px-4 text-center">
        <h2 className="text-3xl font-bold text-white mb-4">
          Ready to Host Your Event?
        </h2>
        <p className="text-gray-400 mb-6">
          Create an event, upload photos, and let AI do the rest.
        </p>
        <Link
          href="/auth/register"
          className="bg-blue-600 text-white px-8 py-3 rounded-lg text-lg hover:bg-blue-700"
        >
          Create Free Account
        </Link>
      </section>
    </div>
  );
}