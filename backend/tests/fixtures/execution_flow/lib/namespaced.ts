import * as auth from "@/lib/auth";

export async function gateSession() {
  return auth.requireSession();
}
