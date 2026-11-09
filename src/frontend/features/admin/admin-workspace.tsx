'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useApi } from '@/features/api/api-provider';
import type { AdminBookingConfiguration } from '@/services/admin-configuration-contracts';
import type { AdminAccessOptions } from '@/services/admin-contracts';
import type { AdminPurpose } from '@/services/admin-catalog-contracts';
import { errorMessage } from '@/services/api/errors';
import {
  configurationDraft,
  type ConfigurationDraft,
} from './booking-configuration/configuration-fields';
import { useAdminMutation, type AdminOperation, type AdminReceipt } from './use-admin-mutation';

function useWorkspaceState() {
  const { session, services } = useApi();
  const mutation = useAdminMutation();
  const [configuration, setConfiguration] = useState<{
    base: AdminBookingConfiguration;
    draft: ConfigurationDraft;
  } | null>(null);
  const [load, setLoad] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [loadError, setLoadError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [purposeEditor, setPurposeEditor] = useState<AdminPurpose | 'create' | null>(null);
  const requests = useRef(new Set<AbortController>());
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current++;
      requests.current.forEach((c) => c.abort());
    },
    [],
  );
  const userId = session.status === 'authenticated' ? session.user.id : '';
  const sessionGeneration =
    session.status === 'authenticated' ? session.user.sessionGeneration : '';
  const readConfiguration = useCallback(async () => {
    const controller = new AbortController();
    requests.current.add(controller);
    const options: AdminAccessOptions = {
      expectedUserId: userId,
      expectedSessionGeneration: sessionGeneration,
      signal: controller.signal,
    };
    try {
      return await services.getAdminBookingConfiguration(options);
    } finally {
      requests.current.delete(controller);
    }
  }, [services, userId, sessionGeneration]);
  const loadConfiguration = useCallback(async () => {
    const version = ++generation.current;
    setLoad('loading');
    setLoadError('');
    try {
      const base = await readConfiguration();
      if (version !== generation.current) return;
      setConfiguration({ base, draft: configurationDraft(base) });
      setLoad('ready');
    } catch (error) {
      if (version !== generation.current) return;
      setLoadError(errorMessage(error));
      setLoad('error');
    }
  }, [readConfiguration]);
  function acceptReceipt(receipt: AdminReceipt) {
    if ('windows' in receipt) {
      setConfiguration({ base: receipt, draft: configurationDraft(receipt) });
      setLoad('ready');
    } else {
      setPurposeEditor(receipt);
      setCatalogRevision((v) => v + 1);
    }
  }
  async function save(operation: AdminOperation, replay = false) {
    const receipt = await mutation.run(operation, replay);
    if (receipt) acceptReceipt(receipt);
    return receipt;
  }
  return {
    mutation,
    save,
    acceptReceipt,
    configuration,
    setConfiguration,
    load,
    loadError,
    loadConfiguration,
    readConfiguration,
    catalogRevision,
    purposeEditor,
    setPurposeEditor,
  };
}
type Workspace = ReturnType<typeof useWorkspaceState>;
const Context = createContext<Workspace | null>(null);
export function AdminWorkspace({ children }: { children: ReactNode }) {
  const state = useWorkspaceState();
  return <Context.Provider value={state}>{children}</Context.Provider>;
}
export function useAdminWorkspace() {
  const value = useContext(Context);
  if (!value) throw new Error('ADMIN workspace is required');
  return value;
}
