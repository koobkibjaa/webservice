// One call that gets a fresh, migrated, seeded database ready to serve. Used by instrumentation.js
// on real boot and by test-helpers/boot.js to stand up an isolated in-process app per test file.
//
// With config.reseedOnBoot false (docker-compose), an existing database is kept as it is: the
// extracted services remember order ids, so the monolith must not hand the same ids out again
// after a restart. `docker compose down -v` resets every database together.
import { getDb } from "./index.js";
import { migrate } from "./migrate.js";
import { seed } from "./seed.js";
import { config } from "../config.js";
import { logger } from "../logger.js";

function alreadyMigrated(db) {
  return Boolean(db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'").get());
}

// Additive changes for a kept database created by an older schema (columns only ever get added).
function upgrade(db) {
  const columns = db.prepare("PRAGMA table_info(events)").all().map((c) => c.name);
  if (!columns.includes("trace_context")) db.exec("ALTER TABLE events ADD COLUMN trace_context TEXT");
}

export function bootstrapDb() {
  const db = getDb();
  if (!config.reseedOnBoot && alreadyMigrated(db)) {
    upgrade(db);
    logger.info("existing database kept (RESEED_ON_BOOT=false)");
    return db;
  }
  migrate(db);
  seed(db);
  logger.info("database migrated and seeded");
  return db;
}
