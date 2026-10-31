export async function connectDB() {
  return true;
}

export async function savePlan(record: { goal: string; userId: string; tasks: unknown[] }) {
  return { id: "plan-1", ...record };
}
