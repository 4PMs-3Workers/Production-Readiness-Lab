'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { ApiServices } from '@/services/api/client';
import { ApiError } from '@/services/api/errors';
import { BookingIntentStore } from '@/services/api/booking-intent';
import type { LoginCredentials, ServiceRequestOptions } from '@/services/contracts';
import type { SessionUser } from '@/types/api';

type Session =
  | { status: 'loading' | 'anonymous' }
  | { status: 'authenticated'; user: SessionUser; verifying?: boolean }
  | { status: 'error'; error: unknown };
interface ApiContextValue {
  services: ApiServices;
  intents: BookingIntentStore;
  session: Session;
  revision: number;
  refreshSession: () => Promise<void>;
  login: (credentials: LoginCredentials, options?: ServiceRequestOptions) => Promise<void>;
  logout: () => Promise<void>;
}
const ApiContext = createContext<ApiContextValue | null>(null);

export function ApiProvider({
  children,
  services: supplied,
}: {
  children: ReactNode;
  services?: ApiServices;
}) {
  const [services] = useState(() => supplied ?? new ApiServices());
  const [intents] = useState(() => new BookingIntentStore());
  const [session, setSession] = useState<Session>({ status: 'loading' });
  const [revision, setRevision] = useState(0);
  const previousUser = useRef<SessionUser | null>(null);
  const clearPrivate = useCallback(() => {
    previousUser.current = null;
    services.clearSession();
    intents.clear();
    setRevision((value) => value + 1);
  }, [services, intents]);
  const acceptUser = useCallback(
    (user: SessionUser) => {
      const previous = previousUser.current;
      if (previous && (previous.id !== user.id || previous.role !== user.role)) clearPrivate();
      previousUser.current = user;
      setSession({ status: 'authenticated', user });
    },
    [clearPrivate],
  );
  const refreshSession = useCallback(async () => {
    services.clearSession();
    setSession({ status: 'loading' });
    try {
      acceptUser(await services.me());
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') return;
      setSession(
        error instanceof ApiError && error.code === 'INVALID_SESSION'
          ? { status: 'anonymous' }
          : { status: 'error', error },
      );
    }
  }, [services, acceptUser]);

  useEffect(() => {
    services.setInvalidSessionHandler(() => {
      clearPrivate();
      setSession({ status: 'anonymous' });
    });
    const controller = new AbortController();
    services.me({ signal: controller.signal }).then(
      (user) => {
        if (!controller.signal.aborted) acceptUser(user);
      },
      (error: unknown) => {
        if (!controller.signal.aborted)
          setSession(
            error instanceof ApiError && error.code === 'INVALID_SESSION'
              ? { status: 'anonymous' }
              : { status: 'error', error },
          );
      },
    );
    return () => {
      controller.abort();
      services.setInvalidSessionHandler();
      services.clearSession();
    };
  }, [services, clearPrivate, acceptUser]);

  // Verify identity when returning from another tab. Hide old private UI while checking.
  useEffect(() => {
    const onFocus = async () => {
      if (session.status !== 'authenticated' || session.verifying) return;
      const previous = session.user;
      setSession({ status: 'authenticated', user: previous, verifying: true });
      try {
        const user = await services.me();
        acceptUser(user);
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') return;
        services.clearSession();
        setSession(
          error instanceof ApiError && error.code === 'INVALID_SESSION'
            ? { status: 'anonymous' }
            : { status: 'error', error },
        );
      }
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [session, services, acceptUser]);

  async function login(credentials: LoginCredentials, options?: ServiceRequestOptions) {
    clearPrivate();
    const user = await services.login(credentials, options);
    acceptUser(user);
  }
  async function logout() {
    clearPrivate();
    setSession({ status: 'loading' });
    try {
      await services.logout();
      setSession({ status: 'anonymous' });
    } catch (error) {
      // Private UI stays cleared. A failure is never reported as successful revocation.
      if (error instanceof ApiError && error.code === 'INVALID_SESSION')
        setSession({ status: 'anonymous' });
      else {
        setSession({ status: 'error', error });
        throw error;
      }
    }
  }
  return (
    <ApiContext.Provider
      value={{ services, intents, session, revision, refreshSession, login, logout }}
    >
      {children}
    </ApiContext.Provider>
  );
}

export function useApi() {
  const value = useContext(ApiContext);
  if (!value) throw new Error('ApiProvider is required.');
  return value;
}
export function useBookingIntent() {
  const { intents } = useApi();
  return useSyncExternalStore(intents.subscribe, intents.snapshot, () => null);
}
