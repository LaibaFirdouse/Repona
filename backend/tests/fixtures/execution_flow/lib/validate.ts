export function validatePlanInput(body: any) {
  if (!body || typeof body.goal !== "string") {
    throw new Error("invalid");
  }
  return { goal: body.goal.trim(), days: Number(body.days) || 7 };
}
