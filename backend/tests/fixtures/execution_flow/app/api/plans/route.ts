import { connectDB } from "@/lib/db";
import { requireSession } from "@/lib/auth";
import { validatePlanInput } from "@/lib/validate";
import { createStudyPlan } from "@/lib/service";
import { NextResponse } from "next/server";

export async function POST(req: Request) {
  await connectDB();
  const session = await requireSession();
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const body = await req.json();
  const input = validatePlanInput(body);
  const plan = await createStudyPlan(input, session.userId);
  return NextResponse.json({ planId: plan.id });
}
