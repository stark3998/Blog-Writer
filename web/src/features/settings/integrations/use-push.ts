import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api/errors';
import { isProblem } from '@/lib/api/problems';
import { apiRequest } from '@/lib/api/request';
import { toastError, toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';
import { useApiQuery } from '@/lib/query/api-query';
import {
  PushSetupError,
  findServiceWorker,
  notificationPermission,
  pushSupport,
  requestNotificationPermission,
  subscribeDevice,
  subscriptionBody,
  subscriptionIdFor,
  type PushSupport,
} from '@/pwa/push';

import { retryUnless, settingsKeys } from '../queries';

/** Where push stands on this device. */
export type DeviceState =
  /** The browser can't receive push, and why. */
  | { readonly kind: 'unsupported'; readonly support: Exclude<PushSupport, 'supported'> }
  /** No service worker is registered here (the development server, or the very first load). */
  | { readonly kind: 'no-worker' }
  /** The owner (or the browser) blocked notifications for this site. */
  | { readonly kind: 'denied' }
  /** It can be turned on. */
  | { readonly kind: 'off' }
  /** Subscribed: this device's server-side id. */
  | { readonly kind: 'on'; readonly subscriptionId: string };

/** Reads this device's push state from the browser. Never throws. */
export async function readDeviceState(): Promise<DeviceState> {
  const support = pushSupport();
  if (support !== 'supported') return { kind: 'unsupported', support };
  if (notificationPermission() === 'denied') return { kind: 'denied' };
  const registration = await findServiceWorker();
  if (!registration) return { kind: 'no-worker' };
  try {
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription || notificationPermission() !== 'granted') return { kind: 'off' };
    return { kind: 'on', subscriptionId: await subscriptionIdFor(subscription.endpoint) };
  } catch {
    return { kind: 'off' };
  }
}

/**
 * Push on this device: its state, the server's key, and turning it on, off or testing it. The
 * subscription goes to the server once; the device finds its id again by hashing its endpoint.
 */
export function usePush() {
  const { api } = useAppData();
  const queryClient = useQueryClient();
  const device = useQuery({
    queryKey: settingsKeys.pushDevice,
    queryFn: readDeviceState,
    staleTime: Infinity,
    // Permission can change in the browser's settings while the owner is away.
    refetchOnWindowFocus: true,
    retry: false,
  });
  const capable = device.data?.kind === 'off' || device.data?.kind === 'on';
  const key = useApiQuery(
    { queryKey: settingsKeys.pushKey, path: '/api/push/key' },
    {
      enabled: capable,
      staleTime: Infinity,
      retry: retryUnless('push_unavailable'),
    },
  );
  const serverReady = key.isSuccess;
  const serverMissing = isProblem(key.error, 'push_unavailable');

  const settle = (state: DeviceState) => {
    queryClient.setQueryData(settingsKeys.pushDevice, state);
    void queryClient.invalidateQueries({ queryKey: settingsKeys.systemStatus });
  };

  const enable = useMutation({
    mutationFn: async (permission: Promise<NotificationPermission>): Promise<DeviceState> => {
      const answer = await permission;
      if (answer === 'denied') return { kind: 'denied' };
      if (answer !== 'granted') {
        throw new PushSetupError('dismissed', 'Notifications stay off: the browser’s question was closed without an answer.');
      }
      const registration = await findServiceWorker();
      if (!registration) return { kind: 'no-worker' };
      const serverKey =
        key.data?.applicationServerKey ?? (await apiRequest(api, 'get', '/api/push/key')).applicationServerKey;
      const subscription = await subscribeDevice(registration, serverKey);
      try {
        await apiRequest(api, 'post', '/api/push/subscriptions', { body: subscriptionBody(subscription) });
      } catch (error) {
        // Keep the device and the server in step: no subscription the server doesn't know.
        await subscription.unsubscribe().catch(() => false);
        throw error;
      }
      return { kind: 'on', subscriptionId: await subscriptionIdFor(subscription.endpoint) };
    },
    onSuccess: (state) => {
      settle(state);
      if (state.kind === 'on') toastSuccess('Notifications are on for this device.');
    },
    onError: (error) => {
      void device.refetch();
      if (error instanceof PushSetupError) toast.error('Couldn’t turn on notifications.', { description: error.message });
      else toastError(error, { title: 'Couldn’t turn on notifications.' });
    },
  });

  const disable = useMutation({
    mutationFn: async (): Promise<DeviceState> => {
      const registration = await findServiceWorker();
      const subscription = await registration?.pushManager.getSubscription();
      if (subscription) {
        const id = await subscriptionIdFor(subscription.endpoint);
        try {
          await apiRequest(api, 'delete', '/api/push/subscriptions/{subscription_id}', {
            params: { path: { subscription_id: id } },
          });
        } catch (error) {
          // Already gone on the server (404): still remove it here.
          if (!(error instanceof ApiError && error.status === 404)) throw error;
        }
        await subscription.unsubscribe();
      }
      return { kind: 'off' };
    },
    onSuccess: (state) => {
      settle(state);
      toastSuccess('Notifications are off for this device.');
    },
    onError: (error) => {
      void device.refetch();
      toastError(error, { title: 'Couldn’t turn off notifications.' });
    },
  });

  /** Sends this device's subscription to the server again (after the server lost it). */
  const register = useMutation({
    mutationFn: async (): Promise<DeviceState> => {
      const registration = await findServiceWorker();
      const subscription = await registration?.pushManager.getSubscription();
      if (!subscription) return { kind: 'off' };
      await apiRequest(api, 'post', '/api/push/subscriptions', { body: subscriptionBody(subscription) });
      return { kind: 'on', subscriptionId: await subscriptionIdFor(subscription.endpoint) };
    },
    onSuccess: (state) => {
      settle(state);
      if (state.kind === 'on') toastSuccess('This device is registered for notifications again.');
    },
    onError: (error) => {
      toastError(error, { title: 'Couldn’t register this device.' });
    },
  });

  return {
    device,
    serverReady,
    serverMissing,
    keyError: serverMissing ? undefined : key.error,
    /** Call from a tap: the permission prompt has to start inside the user's gesture. */
    turnOn: () => {
      // Started here, synchronously, so the prompt belongs to the tap; a browser that throws reads as "no answer".
      enable.mutate(requestNotificationPermission().catch((): NotificationPermission => 'default'));
    },
    turnOff: () => {
      disable.mutate();
    },
    registerAgain: () => {
      register.mutate();
    },
    busy: enable.isPending || disable.isPending || register.isPending,
  };
}
