// React-Query hooks for the training goal. The goal + its progress are refetched
// on window focus (unlike the app-wide default) so a coach-made edit — written
// server-side while the user is away — shows up when they return to the tab.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteGoal,
  getGoal,
  getGoalProgress,
  putGoal,
  type Goal,
  type GoalInput,
  type GoalProgress,
} from "./api";

export const goalKeys = {
  goal: ["goal"] as const,
  progress: ["goal", "progress"] as const,
};

/** The current goal, or `null` when none is set. */
export function useGoal() {
  return useQuery<Goal | null>({
    queryKey: goalKeys.goal,
    queryFn: getGoal,
    refetchOnWindowFocus: true,
  });
}

/** Progress toward the current goal, or `null` when there is no goal. */
export function useGoalProgress() {
  return useQuery<GoalProgress | null>({
    queryKey: goalKeys.progress,
    queryFn: getGoalProgress,
    refetchOnWindowFocus: true,
  });
}

/** Create/update the goal; invalidates both the goal and its progress. */
export function usePutGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<GoalInput>) => putGoal(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: goalKeys.goal });
      qc.invalidateQueries({ queryKey: goalKeys.progress });
    },
  });
}

/** Clear the goal; invalidates both the goal and its progress. */
export function useDeleteGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteGoal,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: goalKeys.goal });
      qc.invalidateQueries({ queryKey: goalKeys.progress });
    },
  });
}
