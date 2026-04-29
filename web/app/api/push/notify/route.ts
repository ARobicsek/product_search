import { NextResponse } from 'next/server';
import { Redis } from '@upstash/redis';
import webpush from 'web-push';

const redis = new Redis({
  url: process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL || '',
  token: process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN || '',
});

// Configure VAPID details
// We use either VAPID_PUBLIC_KEY or NEXT_PUBLIC_VAPID_PUBLIC_KEY
const publicKey = process.env.VAPID_PUBLIC_KEY || process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY || '';
const privateKey = process.env.VAPID_PRIVATE_KEY || '';
const subject = process.env.VAPID_SUBJECT || 'mailto:admin@example.com';

if (publicKey && privateKey) {
  webpush.setVapidDetails(subject, publicKey, privateKey);
}

export async function POST(req: Request) {
  try {
    const authHeader = req.headers.get('Authorization');
    const secret = process.env.PUSH_NOTIFY_SECRET;

    if (!secret || authHeader !== `Bearer ${secret}`) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const payload = await req.json();
    
    // Validate payload
    if (!payload.product || !payload.headline) {
      return NextResponse.json({ error: 'Missing required payload fields: product, headline' }, { status: 400 });
    }

    // Get all subscriptions from Redis
    const subscriptions = await redis.smembers('push_subscriptions');
    
    if (!subscriptions || subscriptions.length === 0) {
      return NextResponse.json({ success: true, sent: 0, message: 'No active subscriptions' });
    }

    const notificationPayload = JSON.stringify({
      title: payload.product,
      body: payload.headline,
      url: payload.url || `/${payload.product}`,
    });

    let sentCount = 0;
    const errors = [];

    // Fan-out push notifications
    for (const subString of subscriptions) {
      try {
        const subscription = typeof subString === 'string' ? JSON.parse(subString) : subString;
        await webpush.sendNotification(subscription, notificationPayload);
        sentCount++;
      } catch (error: any) {
        console.error('Error sending push to a subscription:', error);
        if (error.statusCode === 404 || error.statusCode === 410) {
          // Subscription has expired or is no longer valid, remove it
          await redis.srem('push_subscriptions', typeof subString === 'string' ? subString : JSON.stringify(subString));
        } else {
          errors.push(error.message);
        }
      }
    }

    return NextResponse.json({ 
      success: true, 
      sent: sentCount,
      errors: errors.length > 0 ? errors : undefined
    });
  } catch (error) {
    console.error('Error in notify route:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
