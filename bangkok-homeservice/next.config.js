/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  experimental: {
    // instrumentation.js (boot hook) is still behind a flag in Next 14.2
    instrumentationHook: true,
    // Do NOT bundle the native better-sqlite3 addon into the server/RSC bundles — keep it
    // external so `output: "standalone"` traces the real .node binary instead.
    // amqplib, OpenTelemetry and prom-client stay external too: the instrumentations patch the
    // real modules at runtime, and the metrics registry must be one shared instance.
    serverComponentsExternalPackages: [
      "better-sqlite3", "amqplib", "prom-client",
      "@opentelemetry/api", "@opentelemetry/sdk-node", "@opentelemetry/exporter-trace-otlp-http",
      "@opentelemetry/instrumentation-undici", "@opentelemetry/instrumentation-amqplib",
    ],
  },
};

export default nextConfig;
