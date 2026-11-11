import { useRef, type PointerEvent } from "react";

type Point = { x: number; y: number };
type Gesture = Point & {
  kind: "tap" | "swipe";
  x2: number;
  y2: number;
  width: number;
  height: number;
  duration_ms?: number;
  hold_ms?: number;
};

function position(event: PointerEvent<HTMLElement>): Point {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
    y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
  };
}

function distance(a: Point, b: Point) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

export function useRemoteGesture<T extends HTMLElement>(
  enabled: boolean,
  dimensions: (element: T) => { width: number; height: number },
  send: (gesture: Gesture) => void,
) {
  const active = useRef<{
    id: number;
    origin: Point;
    lastMove: Point;
    startedAt: number;
    movedAt: number;
    size: { width: number; height: number };
  } | null>(null);

  function cancel(event: PointerEvent<T>) {
    if (active.current?.id === event.pointerId) active.current = null;
  }

  return {
    onPointerDown(event: PointerEvent<T>) {
      if (!enabled || !event.isPrimary || event.button !== 0) return;
      const origin = position(event);
      event.currentTarget.setPointerCapture(event.pointerId);
      active.current = {
        id: event.pointerId,
        origin,
        lastMove: origin,
        startedAt: event.timeStamp,
        movedAt: event.timeStamp,
        size: dimensions(event.currentTarget),
      };
    },
    onPointerMove(event: PointerEvent<T>) {
      const gesture = active.current;
      if (!gesture || gesture.id !== event.pointerId) return;
      const point = position(event);
      if (distance(point, gesture.lastMove) >= 0.006) {
        gesture.lastMove = point;
        gesture.movedAt = event.timeStamp;
      }
    },
    onPointerCancel: cancel,
    onLostPointerCapture: cancel,
    onPointerUp(event: PointerEvent<T>) {
      const gesture = active.current;
      if (!gesture || gesture.id !== event.pointerId) return;
      active.current = null;
      if (!enabled) return;

      const end = position(event);
      const size = dimensions(event.currentTarget);
      if (size.width !== gesture.size.width || size.height !== gesture.size.height) return;

      const swipe = distance(end, gesture.origin) > 0.025;
      const hold = distance(end, gesture.lastMove) < 0.006
        ? Math.round(event.timeStamp - gesture.movedAt)
        : 0;
      send({
        kind: swipe ? "swipe" : "tap",
        ...gesture.origin,
        x2: end.x,
        y2: end.y,
        ...size,
        ...(swipe && hold >= 120
          ? {
              duration_ms: Math.max(60, Math.min(1500, Math.round(gesture.movedAt - gesture.startedAt))),
              hold_ms: Math.min(2000, hold),
            }
          : {}),
      });
    },
  };
}
