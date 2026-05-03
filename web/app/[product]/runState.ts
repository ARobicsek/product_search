'use client';

import { useSyncExternalStore } from 'react';

type Listener = (running: boolean) => void;

const listeners = new Set<Listener>();
let running = false;

export function setRunning(value: boolean): void {
  if (running === value) return;
  running = value;
  for (const l of listeners) l(value);
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): boolean {
  return running;
}

function getServerSnapshot(): boolean {
  return false;
}

export function useRunRunning(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
