'use client';

import { useEffect, useState } from 'react';
import { Bell, BellOff } from 'lucide-react';

// Single device-wide push toggle for the home screen. Push delivery is a
// single global subscription set fanned out to every product (see
// /api/push/{subscribe,notify}) — there is intentionally no per-product
// scope, so one bell for the whole device is the correct model. Works in any
// browser with web-push support (the service worker is registered
// unconditionally in layout.tsx); iOS still requires the installed PWA, which
// is a platform constraint, so we degrade to a disabled bell there.

type SubState = 'loading' | 'unsupported' | 'subscribed' | 'unsubscribed';

function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = window.atob(base64);
  const buffer = new ArrayBuffer(raw.length);
  const out = new Uint8Array(buffer);
  for (let i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
  return out;
}

export default function AlertsBell() {
  const [state, setState] = useState<SubState>('loading');
  const [busy, setBusy] = useState(false);

  const publicKey = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;

  useEffect(() => {
    const supported =
      'serviceWorker' in navigator &&
      'PushManager' in window &&
      'Notification' in window;
    if (!supported) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState('unsupported');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const registration = await navigator.serviceWorker.ready;
        const sub = await registration.pushManager.getSubscription();
        if (!cancelled) setState(sub ? 'subscribed' : 'unsubscribed');
      } catch {
        if (!cancelled) setState('unsubscribed');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const subscribe = async () => {
    if (!publicKey) {
      console.error('VAPID public key is missing');
      alert('Push notifications are not configured.');
      return;
    }
    try {
      setBusy(true);
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        throw new Error('Permission not granted for Notification');
      }
      const registration = await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(publicKey),
        });
      }
      const res = await fetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subscription }),
      });
      if (!res.ok) throw new Error('Failed to save subscription on server');
      setState('subscribed');
    } catch (err) {
      console.error('Error subscribing to push:', err);
      alert('Failed to enable alerts.');
    } finally {
      setBusy(false);
    }
  };

  const unsubscribe = async () => {
    try {
      setBusy(true);
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await subscription.unsubscribe();
        await fetch('/api/push/subscribe', {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ subscription }),
        });
      }
      setState('unsubscribed');
    } catch (err) {
      console.error('Error unsubscribing from push:', err);
    } finally {
      setBusy(false);
    }
  };

  const subscribed = state === 'subscribed';
  const disabled = busy || state === 'loading' || state === 'unsupported';
  const label =
    state === 'unsupported'
      ? 'Alerts not supported in this browser'
      : subscribed
        ? 'Turn off alerts'
        : 'Turn on alerts';

  return (
    <button
      type="button"
      onClick={subscribed ? unsubscribe : subscribe}
      disabled={disabled}
      aria-label={label}
      aria-pressed={subscribed}
      className={`shrink-0 inline-flex h-9 w-9 items-center justify-center rounded-full transition focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed ${
        subscribed
          ? 'bg-indigo-600 text-white hover:bg-indigo-700'
          : 'bg-gray-100 text-gray-400 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-500 dark:hover:bg-gray-700'
      } ${disabled ? 'opacity-50' : ''}`}
    >
      {busy || state === 'loading' ? (
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
      ) : subscribed ? (
        <Bell size={18} />
      ) : (
        <BellOff size={18} />
      )}
    </button>
  );
}
