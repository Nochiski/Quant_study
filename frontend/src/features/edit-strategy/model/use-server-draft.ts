import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiRequestError,
  strategyWorkbenchApi,
  type SaveStrategyDraftRequest,
  type StrategyDraft,
} from "../../../shared/api";
import type { DocumentAction, DocumentState } from "./document-state";
import { matchesServerDraftBase, type ServerDraftBase } from "./server-draft";

const SERVER_AUTOSAVE_DELAY_MS = 800;

type Session = {
  draftId: string | null;
  schemaVersion: string | null;
  initialized: boolean;
  phase:
    | "loading"
    | "synced"
    | "saving"
    | "offline"
    | "rejected"
    | "recovery"
    | "conflict";
  version: number;
  persistedSource: string | null;
  updatedAt: string | null;
  remote: StrategyDraft | null;
  incompatible: boolean;
  errorMessage: string | null;
};

export type ServerDraftSync = {
  phase: Session["phase"];
  updatedAt: string | null;
  remote: StrategyDraft | null;
  incompatible: boolean;
  errorMessage: string | null;
  applyRemote: () => void;
  keepLocal: () => void;
  retry: () => void;
};

type ServerDraftOptions = {
  draftId: string | null;
  schemaVersion: string | null;
  schemaPending?: boolean;
  delayMs?: number;
};

const initialSession = (
  draftId: string | null,
  schemaVersion: string | null,
): Session => ({
  draftId,
  schemaVersion,
  initialized: draftId === null,
  phase: "loading",
  version: 0,
  persistedSource: null,
  updatedAt: null,
  remote: null,
  incompatible: false,
  errorMessage: null,
});

const failedSession = (current: Session, error: unknown): Session => {
  const rejected = error instanceof ApiRequestError && error.status !== 0;
  return {
    ...current,
    phase: rejected ? "rejected" : "offline",
    errorMessage: rejected
      ? (error.detail ?? error.code ?? "Draft request was rejected.")
      : null,
  };
};

const baseOf = (
  state: DocumentState,
  schemaVersion: string,
): ServerDraftBase => ({
  format: state.format,
  strategyId: state.strategyId,
  baseRevision: state.baseRevision,
  baseSpecHash: state.baseSpecHash,
  schemaVersion,
});

const requestOf = (
  state: DocumentState,
  schemaVersion: string,
  expectedVersion: number,
): SaveStrategyDraftRequest => ({
  expected_version: expectedVersion,
  source: state.source,
  format: state.format,
  schema_version: schemaVersion,
  strategy_id: state.strategyId,
  base_revision: state.baseRevision,
  base_spec_hash: state.baseSpecHash,
});

/**
 * Server-owned exact-source recovery register. Writes are serialized so a response from this
 * tab always advances the next expected_version; a real second-client race becomes a visible
 * conflict and can never silently overwrite the remote record. Local autosave remains separate.
 */
export const useServerDraft = (
  state: DocumentState,
  dispatch: (action: DocumentAction) => void,
  options: ServerDraftOptions,
): ServerDraftSync => {
  const { draftId, schemaVersion } = options;
  const schemaPending = options.schemaPending ?? false;
  const delayMs = options.delayMs ?? SERVER_AUTOSAVE_DELAY_MS;
  const [session, setSession] = useState<Session>(() =>
    initialSession(draftId, schemaVersion),
  );
  const sessionRef = useRef(session);
  const stateRef = useRef(state);
  const schemaVersionRef = useRef(schemaVersion);
  const writeChain = useRef(Promise.resolve());
  const baseOriginals = useRef(new Map<string, string>());
  const knownVersions = useRef(new Map<string, number>());
  const previousSave = useRef({
    draftId,
    documentEpoch: state.documentEpoch,
    savedVersion: state.savedVersion,
  });

  useEffect(() => {
    stateRef.current = state;
    if (draftId !== null && !baseOriginals.current.has(draftId)) {
      baseOriginals.current.set(draftId, state.savedSource ?? state.source);
    }
  }, [draftId, state]);
  useEffect(() => {
    schemaVersionRef.current = schemaVersion;
  }, [schemaVersion]);
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  const remoteQuery = useQuery({
    queryKey: ["strategy-draft", draftId, schemaVersion],
    queryFn: () => strategyWorkbenchApi.getStrategyDraft(draftId ?? ""),
    enabled: draftId !== null && schemaVersion !== null,
    retry: false,
    staleTime: 0,
  });

  useEffect(() => {
    if (
      draftId === null ||
      schemaVersion === null ||
      (session.draftId === draftId &&
        session.schemaVersion === schemaVersion &&
        session.initialized) ||
      remoteQuery.isPending ||
      remoteQuery.isFetching
    )
      return;
    if (remoteQuery.isError) {
      const absent =
        remoteQuery.error instanceof ApiRequestError &&
        remoteQuery.error.status === 404;
      const empty = {
        ...initialSession(draftId, schemaVersion),
        initialized: true,
        persistedSource: absent
          ? (baseOriginals.current.get(draftId) ?? stateRef.current.source)
          : null,
      } satisfies Session;
      const next = absent
        ? ({ ...empty, phase: "synced" } satisfies Session)
        : failedSession(empty, remoteQuery.error);
      sessionRef.current = next;
      setSession(next);
      return;
    }
    const remote = remoteQuery.data;
    if (!remote) return;
    const compatible = matchesServerDraftBase(
      remote,
      baseOf(stateRef.current, schemaVersion),
    );
    const needsChoice =
      !compatible ||
      remote.source !==
        (baseOriginals.current.get(draftId) ?? stateRef.current.source);
    const next = {
      draftId,
      schemaVersion,
      initialized: true,
      phase: needsChoice ? (compatible ? "recovery" : "conflict") : "synced",
      version: remote.version,
      persistedSource: remote.source,
      updatedAt: remote.updated_at,
      remote: needsChoice ? remote : null,
      incompatible: !compatible,
      errorMessage: null,
    } satisfies Session;
    knownVersions.current.set(draftId, remote.version);
    sessionRef.current = next;
    setSession(next);
  }, [
    draftId,
    remoteQuery.data,
    remoteQuery.error,
    remoteQuery.isError,
    remoteQuery.isFetching,
    remoteQuery.isPending,
    schemaVersion,
    session.draftId,
    session.initialized,
    session.schemaVersion,
  ]);

  const enqueueCurrent = useCallback(() => {
    const requestedId = draftId;
    const requestedState = stateRef.current;
    const requestedSchema = schemaVersionRef.current;
    writeChain.current = writeChain.current.then(async () => {
      const current = sessionRef.current;
      if (
        requestedId === null ||
        requestedSchema === null ||
        current.draftId !== requestedId ||
        current.schemaVersion !== requestedSchema ||
        !current.initialized ||
        current.phase === "offline" ||
        current.phase === "rejected" ||
        current.phase === "conflict" ||
        current.phase === "recovery" ||
        requestedState.composing ||
        current.persistedSource === requestedState.source
      )
        return;
      const saving = { ...current, phase: "saving" } satisfies Session;
      sessionRef.current = saving;
      setSession(saving);
      try {
        const saved = await strategyWorkbenchApi.saveStrategyDraft(
          requestedId,
          requestOf(requestedState, requestedSchema, current.version),
        );
        if (sessionRef.current.draftId !== requestedId) return;
        const next = {
          draftId: requestedId,
          schemaVersion: requestedSchema,
          initialized: true,
          phase: "synced",
          version: saved.version,
          persistedSource: saved.source,
          updatedAt: saved.updated_at,
          remote: null,
          incompatible: false,
          errorMessage: null,
        } satisfies Session;
        knownVersions.current.set(requestedId, saved.version);
        sessionRef.current = next;
        setSession(next);
      } catch (error) {
        if (sessionRef.current.draftId !== requestedId) return;
        if (
          error instanceof ApiRequestError &&
          error.status === 409 &&
          error.code === "strategy.draft.conflict"
        ) {
          const remote = error.currentDraft;
          const compatible =
            remote !== null &&
            matchesServerDraftBase(
              remote,
              baseOf(requestedState, requestedSchema),
            );
          const next = {
            draftId: requestedId,
            schemaVersion: requestedSchema,
            initialized: true,
            phase: "conflict",
            version: remote?.version ?? 0,
            persistedSource: remote?.source ?? null,
            updatedAt: remote?.updated_at ?? null,
            remote,
            incompatible: remote !== null && !compatible,
            errorMessage: null,
          } satisfies Session;
          if (remote !== null)
            knownVersions.current.set(requestedId, remote.version);
          sessionRef.current = next;
          setSession(next);
          return;
        }
        const next = failedSession(current, error);
        sessionRef.current = next;
        setSession(next);
      }
    });
  }, [draftId]);

  useEffect(() => {
    const before = previousSave.current;
    previousSave.current = {
      draftId,
      documentEpoch: state.documentEpoch,
      savedVersion: state.savedVersion,
    };
    if (
      before.draftId === null ||
      before.documentEpoch !== state.documentEpoch ||
      state.savedVersion <= before.savedVersion
    )
      return;
    const retiredId = before.draftId;
    writeChain.current = writeChain.current.then(async () => {
      const version = knownVersions.current.get(retiredId);
      if (version === undefined) return;
      try {
        await strategyWorkbenchApi.deleteStrategyDraft(retiredId, version);
        knownVersions.current.delete(retiredId);
      } catch (error) {
        if (error instanceof ApiRequestError && error.status === 404)
          knownVersions.current.delete(retiredId);
        // A 409 deliberately preserves the newer writer. Network failure leaves recovery intact.
      }
    });
  }, [draftId, state.documentEpoch, state.savedVersion]);

  // Reverting a persisted edit to its immutable base is a server mutation too. Leaving the
  // register behind would resurrect text the user explicitly discarded on the next device.
  useEffect(() => {
    const currentSession = sessionRef.current;
    if (
      draftId === null ||
      schemaVersion === null ||
      currentSession.draftId !== draftId ||
      currentSession.schemaVersion !== schemaVersion ||
      !currentSession.initialized ||
      currentSession.phase !== "synced" ||
      currentSession.version < 1 ||
      state.dirty ||
      state.composing ||
      currentSession.persistedSource === state.source
    )
      return;
    const expectedVersion = currentSession.version;
    const retained = state.source;
    const requestedBase = baseOf(state, schemaVersion);
    const deleting = {
      ...currentSession,
      phase: "saving",
    } satisfies Session;
    // Reserve this CAS operation synchronously so another render cannot enqueue it twice.
    // The visible state update runs inside the serialized external-operation callback.
    sessionRef.current = deleting;
    writeChain.current = writeChain.current.then(async () => {
      if (sessionRef.current !== deleting) return;
      setSession(deleting);
      try {
        await strategyWorkbenchApi.deleteStrategyDraft(
          draftId,
          expectedVersion,
        );
        if (sessionRef.current.draftId !== draftId) return;
        const next = {
          ...sessionRef.current,
          phase: "synced",
          version: 0,
          persistedSource: retained,
          updatedAt: null,
          remote: null,
          incompatible: false,
          errorMessage: null,
        } satisfies Session;
        knownVersions.current.delete(draftId);
        sessionRef.current = next;
        setSession(next);
      } catch (error) {
        if (sessionRef.current.draftId !== draftId) return;
        if (error instanceof ApiRequestError && error.status === 404) {
          const next = {
            ...sessionRef.current,
            phase: "synced",
            version: 0,
            persistedSource: retained,
            updatedAt: null,
            remote: null,
            incompatible: false,
            errorMessage: null,
          } satisfies Session;
          knownVersions.current.delete(draftId);
          sessionRef.current = next;
          setSession(next);
          return;
        }
        if (
          error instanceof ApiRequestError &&
          error.status === 409 &&
          error.code === "strategy.draft.conflict" &&
          error.currentDraft !== null
        ) {
          const remote = error.currentDraft;
          const compatible = matchesServerDraftBase(remote, requestedBase);
          const next = {
            ...sessionRef.current,
            phase: "conflict",
            version: remote.version,
            persistedSource: remote.source,
            updatedAt: remote.updated_at,
            remote,
            incompatible: !compatible,
            errorMessage: null,
          } satisfies Session;
          knownVersions.current.set(draftId, remote.version);
          sessionRef.current = next;
          setSession(next);
          return;
        }
        const next = failedSession(sessionRef.current, error);
        sessionRef.current = next;
        setSession(next);
      }
    });
  }, [draftId, schemaVersion, session, state]);

  useEffect(() => {
    if (
      session.draftId !== draftId ||
      session.schemaVersion !== schemaVersion ||
      !session.initialized ||
      session.phase === "offline" ||
      session.phase === "rejected" ||
      session.phase === "conflict" ||
      session.phase === "recovery" ||
      !state.dirty ||
      state.composing ||
      session.persistedSource === state.source
    )
      return;
    const timer = setTimeout(enqueueCurrent, delayMs);
    return () => clearTimeout(timer);
  }, [
    draftId,
    delayMs,
    enqueueCurrent,
    session.draftId,
    session.initialized,
    session.persistedSource,
    session.phase,
    session.schemaVersion,
    schemaVersion,
    state.composing,
    state.dirty,
    state.source,
  ]);

  const applyRemote = useCallback(() => {
    const current = sessionRef.current;
    if (current.remote === null || current.incompatible) return;
    dispatch({ type: "edit", source: current.remote.source });
    const next = {
      ...current,
      phase: "synced",
      persistedSource: current.remote.source,
      remote: null,
      incompatible: false,
      errorMessage: null,
    } satisfies Session;
    sessionRef.current = next;
    setSession(next);
  }, [dispatch]);

  const keepLocal = useCallback(() => {
    const current = sessionRef.current;
    if (
      (current.phase !== "conflict" && current.phase !== "recovery") ||
      current.incompatible
    )
      return;
    if (!stateRef.current.dirty && current.remote !== null) {
      if (current.schemaVersion === null || current.draftId === null) return;
      const remoteToDelete = current.remote;
      const requestedId = current.draftId;
      const currentSchema = current.schemaVersion;
      const retained = stateRef.current.source;
      const deleting = { ...current, phase: "saving" } satisfies Session;
      sessionRef.current = deleting;
      setSession(deleting);
      writeChain.current = writeChain.current.then(async () => {
        try {
          await strategyWorkbenchApi.deleteStrategyDraft(
            requestedId,
            remoteToDelete.version,
          );
          if (sessionRef.current.draftId !== current.draftId) return;
          const next = {
            ...current,
            phase: "synced",
            version: 0,
            persistedSource: retained,
            updatedAt: null,
            remote: null,
            incompatible: false,
            errorMessage: null,
          } satisfies Session;
          knownVersions.current.delete(requestedId);
          sessionRef.current = next;
          setSession(next);
        } catch (error) {
          if (sessionRef.current.draftId !== current.draftId) return;
          if (error instanceof ApiRequestError && error.status === 404) {
            const next = {
              ...current,
              phase: "synced",
              version: 0,
              persistedSource: retained,
              updatedAt: null,
              remote: null,
              incompatible: false,
              errorMessage: null,
            } satisfies Session;
            knownVersions.current.delete(requestedId);
            sessionRef.current = next;
            setSession(next);
            return;
          }
          if (
            error instanceof ApiRequestError &&
            error.status === 409 &&
            error.code === "strategy.draft.conflict" &&
            error.currentDraft !== null
          ) {
            const remote = error.currentDraft;
            const compatible = matchesServerDraftBase(
              remote,
              baseOf(stateRef.current, currentSchema),
            );
            const next = {
              ...current,
              phase: "conflict",
              version: remote.version,
              persistedSource: remote.source,
              updatedAt: remote.updated_at,
              remote,
              incompatible: !compatible,
              errorMessage: null,
            } satisfies Session;
            knownVersions.current.set(requestedId, remote.version);
            sessionRef.current = next;
            setSession(next);
            return;
          }
          const next = failedSession(current, error);
          sessionRef.current = next;
          setSession(next);
        }
      });
      return;
    }
    const next = {
      ...current,
      phase: "synced",
      persistedSource: current.remote?.source ?? null,
      remote: null,
      incompatible: false,
      errorMessage: null,
    } satisfies Session;
    sessionRef.current = next;
    setSession(next);
  }, []);

  const retry = useCallback(() => {
    if (draftId === null) return;
    const next = initialSession(draftId, schemaVersion);
    sessionRef.current = next;
    setSession(next);
    void remoteQuery.refetch();
  }, [draftId, remoteQuery, schemaVersion]);

  return useMemo(
    () => ({
      phase:
        draftId !== null && schemaVersion === null
          ? schemaPending
            ? "loading"
            : "offline"
          : session.draftId === draftId &&
              session.schemaVersion === schemaVersion
            ? session.phase
            : "loading",
      updatedAt: session.updatedAt,
      remote: session.remote,
      incompatible: session.incompatible,
      errorMessage: session.errorMessage,
      applyRemote,
      keepLocal,
      retry,
    }),
    [
      applyRemote,
      draftId,
      keepLocal,
      retry,
      schemaPending,
      schemaVersion,
      session,
    ],
  );
};
