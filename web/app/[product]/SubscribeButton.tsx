'use client';

import { useState, useEffect } from 'react';
import { Bell, BellOff } from 'lucide-react';

export default function SubscribeButton({ productSlug }: { productSlug: string }) {
  const [isStandalone, setIsStandalone] = useState(false);
  const [isSubscribed, setIsSubscribed] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  // VAPID public key
  const publicKey = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;

  useEffect(() => {
    // Check if running as PWA
    const checkStandalone = () => {
      const isStandaloneMode = window.matchMedia('(display-mode: standalone)').matches 
        || (window.navigator as any).standalone 
        || document.referrer.includes('android-app://');
      setIsStandalone(!!isStandaloneMode);
    };

    checkStandalone();
    window.matchMedia('(display-mode: standalone)').addEventListener('change', checkStandalone);

    // Check existing subscription
    const checkSubscription = async () => {
      if ('serviceWorker' in navigator && 'PushManager' in window) {
        try {
          const registration = await navigator.serviceWorker.ready;
          const subscription = await registration.pushManager.getSubscription();
          setIsSubscribed(!!subscription);
        } catch (error) {
          console.error('Error checking push subscription:', error);
        }
      }
      setIsLoading(false);
    };

    checkSubscription();

    return () => {
      window.matchMedia('(display-mode: standalone)').removeEventListener('change', checkStandalone);
    };
  }, []);

  const urlBase64ToUint8Array = (base64String: string) => {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding)
      .replace(/\-/g, '+')
      .replace(/_/g, '/');

    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  };

  const handleSubscribe = async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      alert('Push notifications are not supported by your browser.');
      return;
    }

    if (!publicKey) {
      console.error('VAPID public key is missing');
      return;
    }

    try {
      setIsLoading(true);
      const permission = await Notification.requestPermission();
      
      if (permission !== 'granted') {
        throw new Error('Permission not granted for Notification');
      }

      const registration = await navigator.serviceWorker.ready;
      
      let subscription = await registration.pushManager.getSubscription();
      
      if (!subscription) {
        const applicationServerKey = urlBase64ToUint8Array(publicKey);
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey,
        });
      }

      // Send subscription to server
      const response = await fetch('/api/push/subscribe', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ subscription }),
      });
      
      if (!response.ok) {
        throw new Error('Failed to save subscription on server');
      }

      setIsSubscribed(true);
    } catch (error) {
      console.error('Error subscribing to push:', error);
      alert('Failed to enable push notifications.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleUnsubscribe = async () => {
    try {
      setIsLoading(true);
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      
      if (subscription) {
        // Unsubscribe from browser push manager
        await subscription.unsubscribe();
        
        // Remove from server
        await fetch('/api/push/subscribe', {
          method: 'DELETE',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ subscription }),
        });
      }
      
      setIsSubscribed(false);
    } catch (error) {
      console.error('Error unsubscribing from push:', error);
    } finally {
      setIsLoading(false);
    }
  };

  // Only show the button if running as PWA (standalone)
  if (!isStandalone) {
    return null;
  }

  return (
    <button
      onClick={isSubscribed ? handleUnsubscribe : handleSubscribe}
      disabled={isLoading}
      className={`flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
        isSubscribed 
          ? 'bg-zinc-100 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700'
          : 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100 dark:bg-indigo-900/30 dark:text-indigo-400 dark:hover:bg-indigo-900/50'
      } disabled:opacity-50`}
    >
      {isLoading ? (
        <div className="h-4 w-4 rounded-full border-2 border-current border-t-transparent animate-spin" />
      ) : isSubscribed ? (
        <BellOff size={16} />
      ) : (
        <Bell size={16} />
      )}
      {isSubscribed ? 'Disable Alerts' : 'Enable Alerts'}
    </button>
  );
}
