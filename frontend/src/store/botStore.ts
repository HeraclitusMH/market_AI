import { create } from 'zustand';
import type { BotState } from '@/types/api';

interface BotStore {
  bot: BotState | null;
  setBot: (bot: BotState) => void;
}

export const useBotStore = create<BotStore>((set) => ({
  bot: null,
  setBot: (bot) => set({ bot }),
}));
