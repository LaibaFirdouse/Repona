export function generateTasks(goal: string, days: number) {
  return Array.from({ length: days }, (_, index) => ({
    day: index + 1,
    title: `${goal} day ${index + 1}`,
  }));
}
