"use client";

import { useState } from "react";

export function PlanForm() {
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(false);

  async function onGenerate() {
    setLoading(true);
    const res = await fetch("/api/plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal: "learn typescript", days: 7 }),
    });
    const data = await res.json();
    setPlan(data);
    setLoading(false);
  }

  return (
    <button onClick={onGenerate} disabled={loading}>
      Generate
    </button>
  );
}
