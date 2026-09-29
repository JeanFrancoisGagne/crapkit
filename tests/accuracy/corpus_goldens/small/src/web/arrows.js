// Sibling arrows in an array, and two arrows that share one source line.
export const handlers = [
  (event) => (event.ctrl ? "copy" : "type"),
  (event) => (event.shift ? "select" : "move"),
];

export const up = (x) => (x > 0 ? (x > 9 ? 2 : 1) : 0), down = (x) => (x < 0 ? (x < -9 ? -2 : -1) : 0);

export function dispatch(event) {
  if (!event) {
    return "none";
  }
  if (event.ctrl && event.shift) {
    return "both";
  }
  return handlers[event.ctrl ? 0 : 1](event);
}
