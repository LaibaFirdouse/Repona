const API_BASE = "https://api.example.com";

export async function loadUser(id: string) {
  return fetch(`${API_BASE}/api/v2/users/${id}`, { method: "GET" });
}

export async function createUser(body: object) {
  return fetch("/api/v2/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function wrongMethod(id: string) {
  return fetch(`/api/v2/users/${id}`, { method: "DELETE" });
}

export async function ambiguousPath() {
  return fetch("/api/v2/items", { method: "GET" });
}
