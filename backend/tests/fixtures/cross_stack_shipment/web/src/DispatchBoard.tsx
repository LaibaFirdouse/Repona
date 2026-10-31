import { enqueueParcel } from "@console/shippingClient";

export function DispatchBoard() {
  async function onDispatch() {
    const ticket = await enqueueParcel({ lane: "east", weightKg: 12 });
    console.log(ticket.trackingCode);
  }
  return <button onClick={onDispatch}>Dispatch</button>;
}
