import { generateTasks } from "@/lib/rules";
import { savePlan } from "@/lib/db";
import { generateTasksAlt } from "@/lib/unused";

export async function createStudyPlan(input: { goal: string; days: number }, userId: string) {
  const tasks = generateTasks(input.goal, input.days);
  return savePlan({ goal: input.goal, userId, tasks });
}

/** Similar name, never called from the API route. */
export async function createStudyPlanPreview(input: { goal: string }) {
  return { id: "preview", goal: input.goal };
}

/** Imported but never invoked — must not become a verified execution hop. */
export function mentionUnusedImport() {
  return typeof generateTasksAlt;
}
