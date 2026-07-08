// React-Query hooks for the multi-goal store. A single data-driven poll
// (useGoals' refetchInterval) replaces any need for a separate "is anything
// building" poll: fast (3s) while any goal's panel is building, slow (30s)
// otherwise — which also naturally surfaces coach-added goals within 30s.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addGoal,
  deleteGoal,
  listGoals,
  refreshGoalPanel,
  updateGoal,
  type Goal,
} from "./api";

export const goalKeys = {
  all: ["goals"] as const,
};

/** All of the user's goals (any status). Polls fast while a panel is building. */
export function useGoals() {
  return useQuery<Goal[]>({
    queryKey: goalKeys.all,
    queryFn: listGoals,
    refetchInterval: (query) => {
      const data = query.state.data as Goal[] | undefined;
      return data?.some((g) => g.panel_status === "building") ? 3000 : 30000;
    },
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}

/** Create a goal; optimistically prepends it so the "building" card shows instantly. */
export function useAddGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ text, sport }: { text: string; sport?: string }) => addGoal(text, sport),
    onSuccess: (newGoal) => {
      qc.setQueryData<Goal[]>(goalKeys.all, (old = []) => [newGoal, ...old]);
      qc.invalidateQueries({ queryKey: goalKeys.all });
    },
  });
}

/** Update a goal's text/sport/status. */
export function useUpdateGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Parameters<typeof updateGoal>[1] }) =>
      updateGoal(id, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: goalKeys.all });
    },
  });
}

/** Permanently delete a goal. */
export function useDeleteGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteGoal(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: goalKeys.all });
    },
  });
}

/** Kick a background panel rebuild; optimistically flips that goal to "building". */
export function useRefreshGoalPanel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => refreshGoalPanel(id),
    onMutate: (id: string) => {
      qc.setQueryData<Goal[]>(goalKeys.all, (old = []) =>
        old.map((g) => (g.id === id ? { ...g, panel_status: "building" } : g)),
      );
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: goalKeys.all });
    },
  });
}
