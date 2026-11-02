// Central environment configuration. Every module reads settings from here — nothing else in the
// codebase touches process.env directly (except db/index.js resolving the db path at open time),
// so the whole config surface is grep-able in one file.
const NODE_ENV = process.env.NODE_ENV || "development";

export const config = Object.freeze({
  nodeEnv: NODE_ENV,
  isTest: NODE_ENV === "test",
  port: parseInt(process.env.PORT, 10) || 3000,
  jwtSecret: process.env.JWT_SECRET || "dev secret - do not use in production",
  jwtExpiresIn: process.env.JWT_EXPIRES_IN || "12h",
  logLevel: process.env.LOG_LEVEL || (NODE_ENV === "test" ? "silent" : "info"),
  bcryptRounds: parseInt(process.env.BCRYPT_ROUNDS, 10) || 10,
  // First order id after a fresh seed. sqlite_sequence is primed to (firstOrderId - 1) so the next
  // AUTOINCREMENT insert lands here, deterministically, on every boot.
  firstOrderId: 8001,
  // One flat tax rate, snapshotted onto each order_item at booking time (tax-excluded).
  taxPercent: 7.0,
  // false = keep an existing database file and seed only an empty one (docker-compose sets this,
  // because the extracted services keep their data across restarts). Default true: drop and
  // reseed on every boot, the original behaviour, still used by `npm run dev` and the tests.
  reseedOnBoot: process.env.RESEED_ON_BOOT !== "false",
  // Where the extracted payment-service answers (in-network address, not through the gateway).
  paymentServiceUrl: process.env.PAYMENT_SERVICE_URL || "http://localhost:8001",
  // The broker the outbox publisher sends domain events to.
  rabbitmqUrl: process.env.RABBITMQ_URL || "amqp://edemand:edemand-dev@localhost:5672",
  // Where the extracted notification-service answers (in-network address, not through the gateway).
  notificationServiceUrl: process.env.NOTIFICATION_SERVICE_URL || "http://localhost:8000",
  // Keycloak issues the tokens the extracted services accept (in-network address).
  keycloakUrl: process.env.KEYCLOAK_URL || "http://localhost:8180",
  keycloakRealm: process.env.KEYCLOAK_REALM || "homeservice",
  keycloakClientId: process.env.KEYCLOAK_CLIENT_ID || "homeservice-monolith",
  keycloakClientSecret: process.env.KEYCLOAK_CLIENT_SECRET || "monolith-dev-secret",
});
