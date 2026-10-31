export type ParcelDraft = { lane: string; weightKg: number };

export type TrackingTicket = { trackingCode: string };

export async function enqueueParcel(draft: ParcelDraft): Promise<TrackingTicket> {
  const response = await fetch("/api/v1/shipments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
  if (!response.ok) {
    throw new Error("shipment rejected");
  }
  return response.json();
}
