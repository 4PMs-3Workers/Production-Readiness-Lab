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
import { StaffChangeIntentStore } from '@/services/api/staff-change-intent';
import { ProposalIntentStore } from '@/services/api/proposal-intent';
import type { LoginCredentials, ServiceRequestOptions } from '@/services/contracts';
import type { SessionUser } from '@/types/api';

type Session =
  | { status: 'loading' | 'anonymous' }
  | { status: 'authenticated'; user: SessionUser; verifying?: boolean }
  | { status: 'error'; error: unknown };
interface ApiContextValue {
  services: ApiServices;
  intents: BookingIntentStore;
  staffChanges: StaffChangeIntentStore;
  proposalIntents: ProposalIntentStore;
  session: Session;
  revision: number;
  refreshSession: () => Promise<void>;
  login: (credentials: LoginCredentials, options?: ServiceRequestOptions) => Promise<SessionUser>;
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
  const [staffChanges] = useState(() => new StaffChangeIntentStore());
  const [proposalIntents] = useState(() => new ProposalIntentStore());
  const [session, setSession] = useState<Session>({ status: 'loading' });
  const [revision, setRevision] = useState(0);
  const previousUser = useRef<SessionUser | null>(null);
  const clearPrivate = useCallback(
    (preserveUnknown = false) => {
      previousUser.current = null;
      services.clearSession();
      if (preserveUnknown) intents.suspendForSessionLoss();
      else intents.clear();
      staffChanges.clearAll();
      proposalIntents.suspend();
      setRevision((value) => value + 1);
    },
    [services, intents, staffChanges, proposalIntents],
  );
  const acceptUser = useCallback(
    (user: SessionUser) => {
      const previous = previousUser.current;
      const pending = intents.snapshot();
      if (
        (previous && (previous.id !== user.id || previous.role !== user.role)) ||
        (pending && (pending.userId !== user.id || user.role !== 'STUDENT'))
      )
        clearPrivate();
      previousUser.current = user;
      if (user.role === 'STUDENT' || user.role === 'STAFF')
        proposalIntents.bindAccount(user.id, user.role);
      else proposalIntents.suspend();
      setSession({ status: 'authenticated', user });
    },
    [clearPrivate, intents, proposalIntents],
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
      clearPrivate(true);
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
      proposalIntents.suspend();
    };
  }, [services, clearPrivate, acceptUser, proposalIntents]);

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
    clearPrivate(true);
    const user = await services.login(credentials, options);
    acceptUser(user);
    return user;
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
      value={{
        services,
        intents,
        staffChanges,
        proposalIntents,
        session,
        revision,
        refreshSession,
        login,
        logout,
      }}
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
