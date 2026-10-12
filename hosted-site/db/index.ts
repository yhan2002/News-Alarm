import { env } from "cloudflare:workers";
import { drizzle } from "drizzle-orm/d1";
import * as schema from "./schema";

export function getDb() {
  if (!env.DB) {
    throw new Error(
      "Cloudflare D1 binding `DB` is unavailable. Configure the `DB` binding in your hosting environment. hosting.example.json only supplies a local placeholder."
    );
  }

  return drizzle(env.DB, { schema });
}
