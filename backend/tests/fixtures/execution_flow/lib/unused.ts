/** Same leaf name as the real service helper, but never imported by the API route. */
export async function createStudyPlanPreviewAlt(input: { goal: string }) {
  return { id: "alt", goal: input.goal };
}

/** Distinct helper that shares a keyword with the real flow without a call edge. */
export function generateTasksAlt(goal: string) {
  return [{ day: 1, title: goal }];
}
