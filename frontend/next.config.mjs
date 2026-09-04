/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // The browser talks to /api on its own origin; Next proxies to FastAPI.
    // Keeps CORS out of the picture in development and lets the two services
    // sit behind one hostname in deployment.
    const backend = process.env.BACKEND_ORIGIN || 'http://127.0.0.1:8000';
    return [{ source: '/api/:path*', destination: `${backend}/api/:path*` }];
  },
};
export default nextConfig;
